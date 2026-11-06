"""Low-stock alerts: a product at or below its reorder level alerts the owner once, until restocked."""

from conftest import SHEET_CALL, SHEET_MAPPING
from test_sheets import KEY, send

from app.mapping import Mapping

HEADER = ["Item #", "Product", "Price (USD)", "Qty on hand", "Show online?", "Reorder at"]
LOW_MAPPING = Mapping.model_validate(
    {
        **SHEET_MAPPING.model_dump(),
        "fields": [
            *SHEET_MAPPING.model_dump()["fields"],
            {
                "source_column": "Reorder at",
                "target": "reorder_level",
                "transform": "integer",
                "confidence": 1,
                "reason": "-",
            },
        ],
    }
)


def sheet(fruit_cake_stock="2"):
    return [
        HEADER,
        ["CAT-1", "Mini Pastelitos Tray", "$38.00", "6", "Yes", "2"],
        ["CAT-2", "Coquito 750ml", "22", "1", "No", "3"],  # low, but not for sale: no alert
        ["CAT-3", "Fruit Cake", "$42.50", fruit_cake_stock, "Yes", "2"],
    ]


def telegram(client):
    return [body["text"] for url, body in client.outbox if "telegram" in url]


def start(client, values):
    client.app.state.service.mapper = lambda table: (LOW_MAPPING, SHEET_CALL)
    mapping_id = send(client, values)["mapping_id"]
    client.post(f"/mappings/{mapping_id}/approve", headers=KEY)
    client.outbox.clear()


def test_a_product_at_its_reorder_level_alerts_once(sheet_client):
    start(sheet_client, sheet())
    send(sheet_client, sheet())
    assert telegram(sheet_client) == ["Running low:\n- Fruit Cake (CAT-3): 2 left, reorder at 2"]

    send(sheet_client, sheet("1"))  # still low: no second message
    assert len(telegram(sheet_client)) == 1


def test_restocking_clears_it_so_the_next_drop_alerts_again(sheet_client):
    start(sheet_client, sheet())
    send(sheet_client, sheet())
    send(sheet_client, sheet("9"))
    send(sheet_client, sheet("0"))
    assert telegram(sheet_client)[-1] == "Running low:\n- Fruit Cake (CAT-3): 0 left, reorder at 2"
    assert len(telegram(sheet_client)) == 2


def test_the_demo_page_lists_low_stock_first_thing(sheet_client):
    start(sheet_client, sheet())
    send(sheet_client, sheet())
    low = sheet_client.get("/demo/api/state").json()["low_stock"]
    assert low == [{"sku": "CAT-3", "title": "Fruit Cake", "quantity": 2, "reorder_level": 2, "image": None}]


def test_adding_a_reorder_level_doesnt_resend_products_to_shopify(sheet_client, store):
    first = [row[:5] for row in sheet()]
    send(sheet_client, first)  # proposes the plain sheet mapping
    sheet_client.post("/mappings/2/approve", headers=KEY)
    send(sheet_client, first)
    writes = len(store.writes)

    start(sheet_client, sheet())  # the new column needs a new mapping
    counts = send(sheet_client, sheet())["sync"]["counts"]
    assert (counts["created"], counts["updated"]) == (0, 0)
    assert len(store.writes) == writes
