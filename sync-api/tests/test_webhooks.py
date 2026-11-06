"""Stock changes in Shopify (online sales, cancellations, admin edits) flow back to the source."""

import base64
import hashlib
import hmac
import json

import pytest
from test_sheets import KEY, SHEET, approve_first_snapshot, send

SECRET = "secret"  # the test app's client secret (conftest)


def deliver(client, payload, delivery="d-1", topic="inventory_levels/update", secret=SECRET):
    body = json.dumps(payload).encode()
    signature = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    headers = {
        "x-shopify-hmac-sha256": signature,
        "x-shopify-topic": topic,
        "x-shopify-webhook-id": delivery,
        "content-type": "application/json",
    }
    return client.post("/webhooks/shopify", content=body, headers=headers)


def stock_event(store, sku, available):
    item = store.by_sku(sku)["item"]
    return {"inventory_item_id": item, "location_id": 1, "available": available}


@pytest.fixture
def synced(sheet_client, store, sheets):
    sheets.execute("TRUNCATE sync.webhook_deliveries")
    approve_first_snapshot(sheet_client)
    send(sheet_client)
    sheet_client.outbox.clear()
    return sheet_client


@pytest.fixture(autouse=True)
def numeric_item_ids(store):
    """Shopify sends the numeric id in webhooks; the sync stores the full gid."""
    real = store.product_set

    def product_set(identifier, data):
        result = real(identifier, data)
        variant = result["productSet"]["product"]["variants"]["nodes"][0]
        number = variant["inventoryItem"]["id"].removeprefix("i")
        for product in store.products.values():
            if product["item"] == variant["inventoryItem"]["id"]:
                product["item"] = number
        if variant["inventoryItem"]["id"] in store.stock:
            store.stock[number] = store.stock.pop(variant["inventoryItem"]["id"])
        variant["inventoryItem"]["id"] = f"gid://shopify/InventoryItem/{number}"
        return result

    store.product_set = product_set


def test_an_online_sale_lowers_the_sheet_and_the_next_sync_sees_no_conflict(synced, store, sheets):
    store.stock[store.by_sku("CAT-1")["item"]] = 4  # two sold online
    response = deliver(synced, stock_event(store, "CAT-1", 4))
    assert response.json() == {"ok": True, "handled": True}

    assert synced.outbox == [
        ("https://hook.make.test/abc", {"sku": "CAT-1", "price": "$38.00", "quantity": "4"})
    ]
    assert sheets.execute(
        """SELECT "Qty on hand" FROM sheets.inventory WHERE "Item #" = 'CAT-1'"""
    ).fetchone() == ("4",)

    # Make wrote 4 into the sheet, so the next snapshot has it: nothing to send, no conflict
    edited = [row[:] for row in SHEET]
    edited[1][3] = "4"
    counts = send(synced, edited)["sync"]["counts"]
    assert (counts["stock_updated"], counts["conflicts"], counts["unchanged"]) == (0, 0, 3)


def test_the_syncs_own_stock_writes_are_ignored(synced, store):
    response = deliver(synced, stock_event(store, "CAT-1", 6))  # what the sync itself set
    assert response.json() == {"ok": True, "handled": True}
    assert synced.outbox == []


def test_a_repeated_delivery_is_handled_once(synced, store):
    deliver(synced, stock_event(store, "CAT-1", 5), delivery="same")
    assert deliver(synced, stock_event(store, "CAT-1", 4), delivery="same").json()["duplicate"] is True
    assert [body["quantity"] for _, body in synced.outbox] == ["5"]


def test_an_unsigned_webhook_is_refused(synced, store):
    assert deliver(synced, stock_event(store, "CAT-1", 0), secret="wrong").status_code == 401
    assert synced.outbox == []


def test_a_failed_write_back_alerts_the_owner(synced, store):
    sent = []

    def make_is_down(url, body):
        sent.append(url)
        if "hook.make" in url:
            raise ConnectionError("Make is down")

    synced.app.state.service.post = make_is_down
    deliver(synced, stock_event(store, "CAT-1", 3))
    assert sent == ["https://hook.make.test/abc", "https://api.telegram.org/botbot-token/sendMessage"]


def test_registering_creates_then_updates_one_subscription(synced, store):
    first = synced.post(
        "/shopify/webhooks", json={"url": "https://a.test/webhooks/shopify"}, headers=KEY
    ).json()
    second = synced.post(
        "/shopify/webhooks", json={"url": "https://b.test/webhooks/shopify"}, headers=KEY
    ).json()
    assert first["id"] == second["id"]
    assert list(store.webhooks.values()) == [{"id": first["id"], "uri": "https://b.test/webhooks/shopify"}]


def test_the_webhook_is_registered_at_startup(db, make_client, store):
    with make_client(shopify_webhook_url="https://sync.test/webhooks/shopify"):
        pass
    assert [hook["uri"] for hook in store.webhooks.values()] == ["https://sync.test/webhooks/shopify"]
