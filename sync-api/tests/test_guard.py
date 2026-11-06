"""Price and stock changes that look like typos are held; the store keeps its old value."""

from decimal import Decimal

from conftest import SHEET
from test_sheets import KEY, approve_first_snapshot, send

from app import guard


def edited(row: int, column: int, value: str) -> list[list]:
    values = [r[:] for r in SHEET]
    values[row][column] = value
    return values


def telegram(client):
    return [body["text"] for url, body in client.outbox if "telegram" in url]


def synced(client):
    approve_first_snapshot(client)
    send(client)
    client.outbox.clear()
    return client


def test_the_rules():
    item = {"last_price": "4.50", "last_quantity": 15}
    assert guard.check({"price": "450.00", "quantity": 15}, item, {})[0]["reason"] == "a 9900% jump"
    assert (
        guard.check({"price": "600.00"}, None, {})[0]["reason"]
        == f"outside ${guard.PRICE_MIN}-${guard.PRICE_MAX}"
    )
    assert guard.check({"price": "5.25", "quantity": 15}, item, {}) == []  # a normal edit
    assert guard.check({"price": "4.50", "quantity": 1515}, item, {})[0]["reason"] == "+1500 units at once"
    assert guard.check({"price": "4.50", "quantity": 0}, item, {}) == []  # selling out is normal
    assert guard.check({"price": "450.00", "quantity": 15}, item, {"price": str(Decimal("450.00"))}) == []


def test_a_typo_price_is_held_and_the_store_keeps_the_old_one(sheet_client, store):
    synced(sheet_client)
    result = send(sheet_client, edited(1, 2, "$380.00"))["sync"]  # $38.00 typed as $380.00
    assert result["counts"]["held"] == 1
    assert store.by_sku("CAT-1")["price"] == "38.00"
    [message] = telegram(sheet_client)
    assert "Mini Pastelitos Tray (CAT-1): price 38.00 -> 380.00: a 900% jump" in message
    assert "/release CAT-1" in message

    held = sheet_client.get("/demo/api/state").json()["held"]
    assert [(h["sku"], h["field"], h["new_value"]) for h in held] == [("CAT-1", "price", "380.00")]

    send(sheet_client, edited(1, 2, "$380.00"))  # still held: no second message
    assert len(telegram(sheet_client)) == 1


def test_fixing_the_typo_clears_the_hold(sheet_client, store):
    synced(sheet_client)
    send(sheet_client, edited(1, 2, "$380.00"))
    send(sheet_client, edited(1, 2, "$39.00"))
    assert sheet_client.get("/demo/api/state").json()["held"] == []
    assert store.by_sku("CAT-1")["price"] == "39.00"


def test_the_owner_can_release_a_change_that_was_intended(sheet_client, store):
    synced(sheet_client)
    send(sheet_client, edited(1, 2, "$380.00"))
    reply = sheet_client.post("/commands", json={"text": "/release cat-1"}, headers=KEY).json()["reply"]
    assert reply.startswith("Released CAT-1 (price).")
    assert store.by_sku("CAT-1")["price"] == "380.00"
    assert sheet_client.get("/demo/api/state").json()["held"] == []


def test_a_reviewer_can_apply_a_held_change_from_the_demo_page(sheet_client, store):
    synced(sheet_client)
    send(sheet_client, edited(1, 2, "$380.00"))
    result = sheet_client.post("/demo/api/release", json={"sku": "CAT-1", "field": "price"}).json()
    assert result["released"] == ["price"]
    assert store.by_sku("CAT-1")["price"] == "380.00"
    assert sheet_client.get("/demo/api/state").json()["held"] == []


def test_applying_one_held_change_leaves_the_others_held(sheet_client, store):
    synced(sheet_client)
    values = edited(1, 2, "$380.00")
    values[1][3] = "9000"
    send(sheet_client, values)
    sheet_client.post("/demo/api/release", json={"sku": "CAT-1", "field": "price"})
    assert store.by_sku("CAT-1")["price"] == "380.00"
    held = sheet_client.get("/demo/api/state").json()["held"]
    assert [h["field"] for h in held] == ["quantity"]


def flag_everything(reason="Far too cheap for a tray"):
    def reviewer(changes):
        reviewer.seen.append(changes)
        flags = [{"sku": c["sku"], "field": c["field"], "reason": reason} for c in changes]
        return flags, {"model": "claude-haiku-5-5", "cost_usd": 0.0001}

    reviewer.seen = []
    return reviewer


def test_claude_holds_a_change_the_rules_let_through(sheet_client, store):
    synced(sheet_client)
    sheet_client.app.state.service.reviewer = reviewer = flag_everything()
    send(sheet_client, edited(1, 2, "$30.00"))  # within the 50% rule
    assert store.by_sku("CAT-1")["price"] == "38.00"
    [held] = sheet_client.get("/demo/api/state").json()["held"]
    assert (held["sku"], held["flagged_by"], held["reason"]) == (
        "CAT-1",
        "claude",
        "Far too cheap for a tray",
    )

    send(sheet_client, edited(1, 2, "$30.00"))  # still held, and Claude isn't asked twice
    assert len(reviewer.seen) == 1
    assert len(sheet_client.get("/demo/api/state").json()["held"]) == 1


def test_a_sync_with_no_changes_doesnt_ask_claude(sheet_client):
    synced(sheet_client)
    sheet_client.app.state.service.reviewer = reviewer = flag_everything()
    send(sheet_client)
    assert reviewer.seen == []


def test_the_sync_goes_on_when_claude_fails(sheet_client, store):
    synced(sheet_client)

    def broken(changes):
        raise RuntimeError("down")

    sheet_client.app.state.service.reviewer = broken
    send(sheet_client, edited(1, 2, "$30.00"))
    assert store.by_sku("CAT-1")["price"] == "30.00"


def test_declining_puts_the_old_value_back_in_the_sheet(sheet_client, store, db):
    synced(sheet_client)
    send(sheet_client, edited(1, 2, "$380.00"))
    sheet_client.outbox.clear()
    result = sheet_client.post("/demo/api/decline", json={"sku": "CAT-1", "field": "price"})
    assert result.status_code == 200
    assert store.by_sku("CAT-1")["price"] == "38.00"
    assert sheet_client.get("/demo/api/state").json()["held"] == []
    [cells] = [body for url, body in sheet_client.outbox if "make" in url]
    assert (cells["sku"], cells["price"]) == ("CAT-1", "$38.00")
    assert db.execute('SELECT "Price (USD)" FROM sheets.inventory WHERE "Item #" = \'CAT-1\'').fetchone() == (
        "$38.00",
    )


def test_a_stock_typo_is_held_but_other_fields_still_sync(sheet_client, store):
    synced(sheet_client)
    values = edited(1, 3, "1600")  # 6 typed as 1600
    values[1][2] = "$39.00"  # and a normal price edit on the same product
    result = send(sheet_client, values)["sync"]
    assert (result["counts"]["held"], result["counts"]["updated"]) == (1, 1)
    assert store.stock[store.by_sku("CAT-1")["item"]] == 6
    assert store.by_sku("CAT-1")["price"] == "39.00"
