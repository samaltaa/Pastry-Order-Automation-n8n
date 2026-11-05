import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app.config import Settings
from app.main import create_app

KEY = {"x-api-key": "test-key"}

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
    ],
    "excluded_rows": [{"key": "SYS", "reason": "System row"}],
}


class FakeStore:
    """A tiny in-memory Shopify that answers the four operations the sync uses."""

    def __init__(self):
        self.products: dict[str, dict] = {}
        self.stock: dict[str, int] = {}
        self.writes: list[str] = []

    def graphql(self, query, variables=None):
        if "locations" in query:
            return {"locations": {"nodes": [{"id": "loc-1"}]}}
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


@pytest.fixture
def db(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("TRUNCATE sync.items, sync.runs, sync.mappings RESTART IDENTITY CASCADE")
        conn.execute("DROP TABLE IF EXISTS pos.sync_test")
        conn.execute(
            "CREATE TABLE pos.sync_test"
            " (codigo TEXT PRIMARY KEY, descripcion TEXT, precio TEXT, existencia NUMERIC, activo CHAR(1))"
        )
        conn.execute(
            """
            INSERT INTO pos.sync_test VALUES
              ('a-1 ', 'TRES LECHES PORCION', '$4.50', 18, 'S'),
              ('A-2', 'FLAN DE COCO', '19', 2, 'N'),
              ('A-3', 'SIN PRECIO', '', 5, 'S'),
              ('SYS', 'AJUSTE', '0', 0, 'N')
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
def client(database_url, store):
    settings = Settings(
        database_url=database_url,
        ops_api_key="test-key",
        shopify_store="demo.myshopify.com",
        shopify_client_id="id",
        shopify_client_secret="secret",
    )
    with TestClient(create_app(settings, shopify=store, mapper=lambda table: None)) as test_client:
        yield test_client


def run(client, **body):
    response = client.post("/sync", json={"table": "sync_test", **body}, headers=KEY)
    assert response.status_code == 200, response.text
    return response.json()


def test_first_run_creates_products_with_stock_and_second_run_does_nothing(db, client, store):
    first = run(client)
    assert first["counts"] == {
        "created": 2,
        "updated": 0,
        "stock_updated": 0,
        "unchanged": 0,
        "excluded": 1,
        "failed": 1,
        "conflicts": 0,
    }
    assert {"sku": "A-3", "action": "failed", "detail": "Missing price"} in first["changes"]
    tres_leches = store.by_sku("A-1")
    assert (tres_leches["title"], tres_leches["price"], tres_leches["status"]) == (
        "Tres Leches Porcion",
        "4.50",
        "ACTIVE",
    )
    assert store.stock[tres_leches["item"]] == 18
    assert store.by_sku("A-2")["status"] == "DRAFT"

    writes = len(store.writes)
    second = run(client)
    assert second["counts"]["unchanged"] == 2
    assert len(store.writes) == writes


def test_price_change_updates_the_product_and_stock_change_only_sets_stock(db, client, store):
    run(client)
    db.execute("UPDATE pos.sync_test SET precio = '$5.00' WHERE codigo = 'A-2'")
    db.execute("UPDATE pos.sync_test SET existencia = 12 WHERE codigo = 'a-1 '")
    store.writes.clear()

    report = run(client)
    assert report["counts"]["updated"] == 1
    assert report["counts"]["stock_updated"] == 1
    assert store.writes == ["productSet A-2", f"stock {store.by_sku('A-1')['item']}=12"]
    assert store.by_sku("A-2")["price"] == "5.00"


def test_a_sale_in_shopify_is_reported_as_a_conflict_and_the_pos_wins(db, client, store):
    run(client)
    item = store.by_sku("A-1")["item"]
    store.stock[item] = 15  # three sold online since the last sync
    db.execute("UPDATE pos.sync_test SET existencia = 10 WHERE codigo = 'a-1 '")

    report = run(client)
    assert report["counts"]["conflicts"] == 1
    change = next(c for c in report["changes"] if c["sku"] == "A-1")
    assert change["detail"] == "Shopify had 15, expected 18; POS value 10 applied"
    assert store.stock[item] == 10


def test_dry_run_changes_nothing(db, client, store):
    report = run(client, dry_run=True)
    assert report["counts"]["created"] == 2
    assert store.writes == []
    assert db.execute("SELECT COUNT(*) FROM sync.items").fetchone() == (0,)


def test_runs_are_recorded(db, client):
    run(client)
    runs = client.get("/sync/runs", headers=KEY).json()
    assert runs[0]["status"] == "succeeded"
    assert (runs[0]["created"], runs[0]["failed"]) == (2, 1)


def test_needs_an_approved_mapping(db, client):
    db.execute("UPDATE sync.mappings SET status = 'rejected'")
    assert client.post("/sync", json={"table": "sync_test"}, headers=KEY).status_code == 409


def test_only_one_sync_at_a_time(db, client):
    db.execute("SELECT pg_advisory_lock(7270002)")
    try:
        response = client.post("/sync", json={"table": "sync_test"}, headers=KEY)
        assert response.status_code == 409
        assert response.json()["detail"] == "A sync is already running"
    finally:
        db.execute("SELECT pg_advisory_unlock(7270002)")
