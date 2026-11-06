import time

import pytest
from conftest import SHEET

from app.sources import BadSheet, read_rows, store_sheet

KEY = {"x-api-key": "test-key"}
SHEET_KEY = {"x-api-key": "sheet-key"}


def send(client, values=SHEET):
    response = client.post(
        "/sources/sheets/inventory", json={"range": "A1:E9", "values": values}, headers=SHEET_KEY
    )
    assert response.status_code == 200, response.text
    return response.json()


def approve_first_snapshot(client):
    mapping_id = send(client)["mapping_id"]
    assert client.post(f"/mappings/{mapping_id}/approve", headers=KEY).status_code == 200


def test_a_snapshot_is_stored_as_a_table_with_the_sheet_columns(sheets):
    columns = store_sheet(sheets, "inventory", SHEET)
    assert columns == SHEET[0]
    rows = read_rows(sheets, "sheets.inventory")
    assert len(rows) == 4  # the blank row is dropped
    assert rows[2] == {
        "Item #": "CAT-3",
        "Product": "Fruit Cake",
        "Price (USD)": "$42.50",
        "Qty on hand": "5",
        "Show online?": None,
    }


def test_new_headers_replace_the_table(sheets):
    store_sheet(sheets, "inventory", SHEET)
    store_sheet(sheets, "inventory", [["SKU", "Name"], ["A", "Flan"]])
    assert read_rows(sheets, "sheets.inventory") == [{"SKU": "A", "Name": "Flan"}]


@pytest.mark.parametrize(
    "values",
    [
        [["Item #", "Product"]],  # no data
        [["Item #", "", "Price"], ["A", "B", "C"]],  # blank header in the middle
        [["Item #", "Item #"], ["A", "B"]],  # duplicate header
    ],
)
def test_bad_sheets_are_refused(sheets, values):
    with pytest.raises(BadSheet):
        store_sheet(sheets, "inventory", values)


def test_ingest_needs_its_own_key(sheet_client):
    response = sheet_client.post("/sources/sheets/inventory", json={"values": SHEET}, headers=KEY)
    assert response.status_code == 401


def test_ingest_is_off_without_a_key(db, make_client):
    with make_client() as client:
        assert client.post("/sources/sheets/inventory", json={"values": SHEET}).status_code == 404


def test_first_snapshot_asks_the_owner_to_approve_a_mapping(sheet_client, calls, store):
    result = send(sheet_client)
    assert result == {"status": "needs_approval", "mapping_id": 2}
    assert store.products == {}
    [(url, message)] = sheet_client.outbox
    assert url == "https://api.telegram.org/botbot-token/sendMessage"
    assert message["chat_id"] == "42"
    assert message["text"].startswith("The inventory sheet needs a mapping. Its sync is paused")
    assert "- failed: Small effect on the store: nothing in Shopify to compare with yet" in message["text"]
    assert "Reply /approve 2 or /reject 2" in message["text"]

    # Another edit while the proposal waits: no second Claude call, no second message
    assert send(sheet_client)["mapping_id"] == 2
    assert len(calls) == 1
    assert len(sheet_client.outbox) == 1


def test_approved_sheet_syncs_on_every_snapshot(sheet_client, store):
    approve_first_snapshot(sheet_client)
    result = send(sheet_client)
    assert result["status"] == "synced"
    assert result["sync"]["counts"]["created"] == 3
    assert store.by_sku("CAT-2")["status"] == "DRAFT"

    edited = [row[:] for row in SHEET]
    edited[1][3] = "4"
    counts = send(sheet_client, edited)["sync"]["counts"]
    assert (counts["stock_updated"], counts["unchanged"]) == (1, 2)


def test_renamed_column_pauses_the_sync_until_a_new_mapping_is_approved(sheet_client, calls, store):
    approve_first_snapshot(sheet_client)
    send(sheet_client)
    renamed = [["Item #", "Product", "Price (USD)", "Stock", "Show online?"], *SHEET[1:]]
    renamed[1] = ["CAT-1", "Mini Pastelitos Tray", "$99.00", "6", "Yes"]

    result = send(sheet_client, renamed)
    assert result["status"] == "needs_approval"
    assert len(calls) == 2
    assert "columns changed. Its sync is paused" in sheet_client.outbox[-1][1]["text"]
    assert store.by_sku("CAT-1")["price"] == "38.00"  # nothing synced


def test_a_sheet_that_isnt_the_active_source_is_only_stored(sheets, make_client, calls):
    with make_client(sheet_ingest_key="sheet-key", mapper=lambda table: calls.append(table)) as client:
        result = client.post("/sources/sheets/inventory", json={"values": SHEET}, headers=SHEET_KEY).json()
    assert result["status"] == "stored"
    assert calls == []


def test_a_deleted_row_goes_to_draft_and_comes_back(sheet_client, store):
    approve_first_snapshot(sheet_client)
    send(sheet_client)
    without_fruit_cake = [row for row in SHEET if row[:1] != ["CAT-3"]]

    result = send(sheet_client, without_fruit_cake)["sync"]
    assert result["counts"]["removed"] == 1
    assert store.by_sku("CAT-3")["status"] == "DRAFT"
    assert send(sheet_client, without_fruit_cake)["sync"]["counts"]["removed"] == 0  # already drafted

    result = send(sheet_client)["sync"]
    assert result["counts"]["updated"] == 1
    assert store.by_sku("CAT-3")["status"] == "ACTIVE"


def test_a_mostly_empty_sheet_removes_nothing(sheet_client, store):
    approve_first_snapshot(sheet_client)
    send(sheet_client)
    result = send(sheet_client, SHEET[:2])["sync"]
    assert result["counts"]["removed"] == 0
    assert "Nothing was removed" in result["changes"][-1]["detail"]
    assert not any(write.startswith("draft") for write in store.writes)


def test_demo_edits_are_written_back_to_the_sheet(sheet_client, store):
    approve_first_snapshot(sheet_client)
    send(sheet_client)
    edits = {"edits": [{"key": "CAT-1", "price": 40, "quantity": 3}]}

    result = sheet_client.post("/demo/api/sync", json=edits).json()
    assert result["sheet"] == {"written": ["CAT-1"], "failed": []}
    assert sheet_client.outbox[-1] == (
        "https://hook.make.test/abc",
        {"sku": "CAT-1", "price": "$40.00", "quantity": "3"},
    )
    assert store.by_sku("CAT-1")["price"] == "40.00"

    # The next snapshot from the sheet now matches: nothing to send
    edited = [row[:] for row in SHEET]
    edited[1][2:4] = ["$40.00", "3"]
    assert send(sheet_client, edited)["sync"]["counts"]["unchanged"] == 3


def test_a_failed_write_back_is_reported(sheet_client):
    approve_first_snapshot(sheet_client)
    send(sheet_client)

    def broken(url, body):
        raise ConnectionError("Make is down")

    sheet_client.app.state.service.post = broken
    result = sheet_client.post("/demo/api/sync", json={"edits": [{"key": "CAT-2", "quantity": 1}]}).json()
    assert result["sheet"] == {"written": [], "failed": ["CAT-2"]}
    assert result["counts"]["stock_updated"] == 1  # the store still got the change


def test_two_arrivals_at_once_ask_claude_only_once(sheet_client, calls):
    """One edit can make Make send the sheet twice, a moment apart."""
    from concurrent.futures import ThreadPoolExecutor

    service = sheet_client.app.state.service
    ask = service.mapper

    def slow_mapper(table):
        time.sleep(0.5)  # the second arrival comes in while Claude is still answering
        return ask(table)

    service.mapper = slow_mapper
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: send(sheet_client), range(2)))
    assert {result["mapping_id"] for result in results} == {2}
    assert len(calls) == 1
    assert len(sheet_client.outbox) == 1
