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


def test_a_stock_typo_is_held_but_other_fields_still_sync(sheet_client, store):
    synced(sheet_client)
    values = edited(1, 3, "1600")  # 6 typed as 1600
    values[1][2] = "$39.00"  # and a normal price edit on the same product
    result = send(sheet_client, values)["sync"]
    assert (result["counts"]["held"], result["counts"]["updated"]) == (1, 1)
    assert store.stock[store.by_sku("CAT-1")["item"]] == 6
    assert store.by_sku("CAT-1")["price"] == "39.00"
