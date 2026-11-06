import os
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app.config import Settings
from app.main import create_app
from app.mapping import Mapping

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"


@pytest.fixture(scope="session")
def database_url():
    """A throwaway database with every migration applied. The migrations are idempotent."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    with psycopg.connect(url, autocommit=True) as conn:
        for migration in sorted(MIGRATIONS.glob("*.sql")):
            conn.execute(migration.read_text())
        conn.execute("CREATE SCHEMA IF NOT EXISTS pos")
        conn.execute("DROP TABLE IF EXISTS pos.test_items")
        conn.execute(
            "CREATE TABLE pos.test_items"
            " (codigo VARCHAR(20) PRIMARY KEY, descripcion TEXT, precio TEXT, activo CHAR(1))"
        )
        conn.execute(
            """
            INSERT INTO pos.test_items VALUES
              ('A-1', 'TRES LECHES PORCION', '$4.50', 'S'),
              ('A-2', 'FLAN DE COCO', '19', 'N'),
              ('SYS', 'AJUSTE', '0', 'N')
            """
        )
    return url


MAPPING = {
    "key_column": "codigo",
    "fields": [
        {
            "source_column": "codigo",
            "target": "sku",
            "transform": "trim_upper",
            "confidence": 1,
            "reason": "-",
        },
        {
            "source_column": "descripcion",
            "target": "title",
            "transform": "title_case",
            "confidence": 1,
            "reason": "-",
        },
        {"source_column": "precio", "target": "price", "transform": "money", "confidence": 1, "reason": "-"},
        {
            "source_column": "existencia",
            "target": "quantity",
            "transform": "integer",
            "confidence": 1,
            "reason": "-",
        },
        {
            "source_column": "activo",
            "target": "status",
            "transform": "active_status",
            "confidence": 1,
            "reason": "-",
        },
        {"source_column": "foto", "target": "image", "transform": "trim", "confidence": 1, "reason": "-"},
    ],
    "excluded_rows": [{"key": "SYS", "reason": "System row"}],
}


class FakeStore:
    """A tiny in-memory Shopify that answers the four operations the sync uses."""

    def __init__(self):
        self.products: dict[str, dict] = {}
        self.stock: dict[str, int] = {}
        self.writes: list[str] = []
        self.published: set[str] = set()
        self.webhooks: dict[str, dict] = {}

    def graphql(self, query, variables=None):
        if "locations" in query:
            return {"locations": {"nodes": [{"id": "loc-1"}]}}
        if "publishablePublish" in query:
            assert variables["input"] == [{"publicationId": "pub-online-store"}]
            self.published.add(variables["id"])
            return {"publishablePublish": {"userErrors": []}}
        if "publications" in query:
            channels = [
                {"id": "pub-pos", "name": "Point of Sale"},
                {"id": "pub-online-store", "name": "Online Store"},
            ]
            return {"publications": {"nodes": channels}}
        if "webhookSubscriptions(" in query:
            return {"webhookSubscriptions": {"nodes": list(self.webhooks.values())}}
        if "webhookSubscriptionCreate" in query:
            hook = {"id": f"wh{len(self.webhooks) + 1}", "uri": variables["webhook"]["uri"]}
            self.webhooks[hook["id"]] = hook
            return {"webhookSubscriptionCreate": {"webhookSubscription": hook, "userErrors": []}}
        if "webhookSubscriptionUpdate" in query:
            self.webhooks[variables["id"]]["uri"] = variables["webhook"]["uri"]
            hook = self.webhooks[variables["id"]]
            return {"webhookSubscriptionUpdate": {"webhookSubscription": hook, "userErrors": []}}
        if "productUpdate" in query:
            product = self.products[variables["product"]["id"]]
            product["status"] = variables["product"]["status"]
            self.writes.append(f"draft {product['sku']}")
            return {"productUpdate": {"userErrors": []}}
        if "productSet" in query:
            return self.product_set(variables["identifier"], variables["input"])
        if "inventorySetQuantities" in query:
            return self.set_stock(variables["input"]["quantities"][0])
        if "inventoryItem(" in query:
            quantity = self.stock[variables["id"]]
            return {
                "inventoryItem": {
                    "inventoryLevel": {"quantities": [{"name": "available", "quantity": quantity}]}
                }
            }
        raise AssertionError(f"Unexpected query: {query}")

    def product_set(self, identifier, data):
        found = next(
            (
                p
                for p in self.products.values()
                if identifier.get("id") == p["id"] or identifier.get("handle") == p["handle"]
            ),
            None,
        )
        if found is None:
            number = len(self.products) + 1
            found = {
                "id": f"p{number}",
                "variant": f"v{number}",
                "item": f"i{number}",
                "handle": data["handle"],
            }
            self.products[found["id"]] = found
        variant = data["variants"][0]
        found.update(
            title=data["title"], price=variant["price"], sku=variant["sku"], status=data.get("status")
        )
        if "files" in data:
            found["images"] = [file["originalSource"] for file in data["files"]]
            self.writes.append(f"images {variant['sku']}")
        for level in variant.get("inventoryQuantities", []):
            self.stock[found["item"]] = level["quantity"]
        self.writes.append(f"productSet {variant['sku']}")
        product = {
            "id": found["id"],
            "variants": {"nodes": [{"id": found["variant"], "inventoryItem": {"id": found["item"]}}]},
        }
        return {"productSet": {"product": product, "userErrors": []}}

    def set_stock(self, quantity):
        item = quantity["inventoryItemId"]
        expected = quantity["changeFromQuantity"]
        if expected is not None and self.stock.get(item) != expected:
            error = {"field": ["input"], "message": "stale", "code": "CHANGE_FROM_QUANTITY_STALE"}
            return {"inventorySetQuantities": {"userErrors": [error]}}
        self.stock[item] = quantity["quantity"]
        self.writes.append(f"stock {item}={quantity['quantity']}")
        return {"inventorySetQuantities": {"userErrors": []}}

    def by_sku(self, sku):
        return next(p for p in self.products.values() if p["sku"] == sku)

    def close(self):
        pass


class Outbox(list):
    """Records outgoing JSON POSTs (Telegram, Make) instead of sending them."""

    def __call__(self, url, body):
        self.append((url, body))


@pytest.fixture
def db(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            "TRUNCATE sync.items, sync.runs, sync.mappings, sync.low_stock, sync.webhook_deliveries,"
            " sync.held_changes RESTART IDENTITY CASCADE"
        )
        conn.execute("DROP TABLE IF EXISTS pos.sync_test")
        conn.execute(
            "CREATE TABLE pos.sync_test"
            " (codigo TEXT PRIMARY KEY, descripcion TEXT, precio TEXT, existencia NUMERIC, activo CHAR(1),"
            "  foto TEXT)"
        )
        conn.execute(
            """
            INSERT INTO pos.sync_test VALUES
              ('a-1 ', 'TRES LECHES PORCION', '$4.50', 18, 'S', 'https://img.test/tres-leches.jpg'),
              ('A-2', 'FLAN DE COCO', '19', 2, 'N', NULL),
              ('A-3', 'SIN PRECIO', '', 5, 'S', NULL),
              ('SYS', 'AJUSTE', '0', 0, 'N', NULL)
            """
        )
        conn.execute(
            "INSERT INTO sync.mappings (source_table, mapping, status, reviewed_at)"
            " VALUES ('pos.sync_test', %s, 'approved', NOW())",
            (Jsonb(MAPPING),),
        )
        yield conn


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def make_client(database_url, store):
    """Builds a test client against the fake store. Pass a fake mapper and any setting overrides."""

    def build(mapper=lambda table: None, post=None, **overrides):
        settings = Settings(
            database_url=database_url,
            ops_api_key="test-key",
            shopify_store="demo.myshopify.com",
            shopify_client_id="id",
            shopify_client_secret="secret",
            # Explicit, so nothing from a developer's .env (keys, Telegram) leaks into the tests
            **{
                "sync_source": "sync_test",
                "sheet_ingest_key": None,
                "make_sheet_webhook_url": None,
                "sheet_url": None,
                "telegram_bot_token": None,
                "owner_telegram_chat_id": None,
                "storefront_url": None,
                "storefront_password": None,
                "shopify_webhook_url": None,
                **overrides,
            },
        )
        return TestClient(
            create_app(settings, shopify=store, mapper=mapper, post=Outbox() if post is None else post)
        )

    return build


# A Google Sheet as Make sends it, and the active-source client that receives it
SHEET_CALL = {"model": "claude-test", "input_tokens": 1000, "output_tokens": 200, "latency_ms": 900}
SHEET = [
    ["Item #", "Product", "Price (USD)", "Qty on hand", "Show online?"],
    ["CAT-1", "Mini Pastelitos Tray", "$38.00", "6", "Yes"],
    ["CAT-2", "Coquito 750ml", "22", "10", "No"],
    ["CAT-3", "Fruit Cake", "$42.50", "5"],  # Google leaves out trailing empty cells
    [],
    ["", "TOTAL", "", "21"],
]
SHEET_MAPPING = Mapping.model_validate(
    {
        "key_column": "Item #",
        "fields": [
            {
                "source_column": "Item #",
                "target": "sku",
                "transform": "trim_upper",
                "confidence": 1,
                "reason": "-",
            },
            {
                "source_column": "Product",
                "target": "title",
                "transform": "trim",
                "confidence": 1,
                "reason": "-",
            },
            {
                "source_column": "Price (USD)",
                "target": "price",
                "transform": "money",
                "confidence": 1,
                "reason": "-",
            },
            {
                "source_column": "Qty on hand",
                "target": "quantity",
                "transform": "integer",
                "confidence": 1,
                "reason": "-",
            },
            {
                "source_column": "Show online?",
                "target": "status",
                "transform": "active_status",
                "confidence": 1,
                "reason": "-",
            },
        ],
        "excluded_rows": [{"key": "", "reason": "Totals row"}],
    }
)


@pytest.fixture
def sheets(db):
    db.execute("DROP SCHEMA IF EXISTS sheets CASCADE")
    db.execute("CREATE SCHEMA sheets")
    return db


@pytest.fixture
def calls():
    return []


@pytest.fixture
def sheet_client(sheets, make_client, calls):
    """The inventory sheet is the active source, with Telegram and a Make webhook configured."""
    outbox = Outbox()

    def mapper(table):
        calls.append(table)
        return SHEET_MAPPING, SHEET_CALL

    client = make_client(
        mapper=mapper,
        post=outbox,
        sync_source="sheets.inventory",
        sheet_ingest_key="sheet-key",
        telegram_bot_token="bot-token",
        owner_telegram_chat_id="42",
        make_sheet_webhook_url="https://hook.make.test/abc",
        demo_enabled=True,
    )
    with client:
        client.outbox = outbox
        yield client
