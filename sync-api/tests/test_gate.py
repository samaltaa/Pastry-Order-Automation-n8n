"""Deterministic approval: a new mapping is applied without a person only if every check passes."""

from conftest import SHEET, SHEET_CALL, SHEET_MAPPING
from test_sheets import KEY, send

from app import gate
from app.mapping import Mapping


def renamed(mapping: Mapping, old: str, new: str, **changes) -> Mapping:
    """The same mapping with one column renamed, and optionally other field changes by column."""
    data = mapping.model_dump()
    for field in data["fields"]:
        if field["source_column"] == old:
            field["source_column"] = new
        field.update(changes.get(field["source_column"], {}))
    return Mapping.model_validate(data)


def with_header(old: str, new: str) -> list[list]:
    return [[new if name == old else name for name in SHEET[0]], *SHEET[1:]]


def start(client):
    """The sheet is synced with an owner-approved mapping, as it would be in production."""
    mapping_id = send(client)["mapping_id"]
    client.post(f"/mappings/{mapping_id}/approve", headers=KEY)
    send(client)
    client.outbox.clear()


def propose(client, mapping):
    client.app.state.service.mapper = lambda table: (mapping, SHEET_CALL)


def by_name(checks):
    return {item["name"]: item for item in checks}


def test_a_renamed_column_with_the_same_meaning_is_approved_automatically(sheet_client, store):
    start(sheet_client)
    propose(sheet_client, renamed(SHEET_MAPPING, "Qty on hand", "Stock"))

    result = send(sheet_client, with_header("Qty on hand", "Stock"))
    assert result["status"] == "auto_approved"
    assert result["sync"]["counts"]["failed"] == 0
    [message] = [body["text"] for url, body in sheet_client.outbox if "telegram" in url]
    assert "Every check passed, so mapping 3 was approved and synced." in message
    assert "Reply /approve" not in message


def test_an_inverted_flag_is_held_for_the_owner(sheet_client, store):
    start(sheet_client)
    inverted = renamed(
        SHEET_MAPPING, "Show online?", "Hide online?", **{"Hide online?": {"transform": "inactive_status"}}
    )
    propose(sheet_client, inverted)

    result = send(sheet_client, with_header("Show online?", "Hide online?"))
    assert result["status"] == "needs_approval"
    [message] = [body["text"] for url, body in sheet_client.outbox if "telegram" in url]
    assert "- failed: Small effect on the store: would change 2 of 3 products (67%)" in message
    assert store.by_sku("CAT-1")["status"] == "ACTIVE"  # nothing synced


def test_the_checks_on_their_own():
    rows = [
        {
            "Item #": "CAT-1",
            "Product": "Tray",
            "Price (USD)": "$38.00",
            "Qty on hand": "6",
            "Show online?": "Yes",
        },
        {
            "Item #": "CAT-1",
            "Product": "Tray",
            "Price (USD)": "call us",
            "Qty on hand": "lots",
            "Show online?": "Yes",
        },
    ]
    columns = list(rows[0])
    unsure = renamed(SHEET_MAPPING, "Product", "Product", **{"Product": {"confidence": 0.5}})
    swapped = renamed(SHEET_MAPPING, "Product", "Product", **{"Product": {"target": "product_type"}})
    checks = by_name(gate.check(unsure, columns, rows, previous=SHEET_MAPPING, known={}))

    assert checks["Structure"]["passed"]
    assert not checks["Confidence"]["passed"]
    assert "Price (USD) 'call us' isn't a valid price" in checks["Every row reads cleanly"]["detail"]
    assert checks["SKUs are unique"]["detail"] == "CAT-1 appears more than once"
    assert checks["Same meaning as before"]["passed"]
    assert not checks["Small effect on the store"]["passed"]  # nothing to compare with
    meaning = by_name(gate.check(swapped, columns, rows[:1], previous=SHEET_MAPPING, known={}))
    assert meaning["Same meaning as before"]["detail"] == "Product was title, now product_type"


def test_an_old_proposal_overtaken_by_an_approval_isnt_reused(sheet_client):
    """What happened live: one edit sent the sheet twice, two identical proposals were made, the newer
    one was approved, and the older one stayed "proposed"."""
    stale = send(sheet_client)["mapping_id"]
    with sheet_client.app.state.service.pool.connection() as conn:
        newer = conn.execute(
            "INSERT INTO sync.mappings (source_table, mapping, status)"
            " SELECT source_table, mapping, 'proposed' FROM sync.mappings WHERE id = %s RETURNING id",
            (stale,),
        ).fetchone()[0]
    sheet_client.post(f"/mappings/{newer}/approve", headers=KEY)
    send(sheet_client)
    propose(sheet_client, renamed(SHEET_MAPPING, "Qty on hand", "Stock"))
    assert send(sheet_client, with_header("Qty on hand", "Stock"))["status"] == "auto_approved"

    propose(sheet_client, SHEET_MAPPING)  # renamed back: the stale proposal must not be picked up
    result = send(sheet_client)
    assert result["status"] == "auto_approved"
    assert result["mapping_id"] != stale
