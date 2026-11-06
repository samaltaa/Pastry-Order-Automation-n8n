import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm import MappingFailed
from app.main import create_app
from app.mapping import Mapping

KEY = {"x-api-key": "test-key"}

GOOD_MAPPING = Mapping.model_validate(
    {
        "key_column": "codigo",
        "fields": [
            {
                "source_column": "codigo",
                "target": "sku",
                "transform": "trim_upper",
                "confidence": 0.95,
                "reason": "Code",
            },
            {
                "source_column": "descripcion",
                "target": "title",
                "transform": "title_case",
                "confidence": 0.9,
                "reason": "Name",
            },
            {
                "source_column": "precio",
                "target": "price",
                "transform": "money",
                "confidence": 0.9,
                "reason": "Price",
            },
            {
                "source_column": "activo",
                "target": "status",
                "transform": "active_status",
                "confidence": 0.6,
                "reason": "Flag",
            },
        ],
        "excluded_rows": [{"key": "SYS", "reason": "System row"}],
    }
)
CALL = {"model": "fake", "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.0, "latency_ms": 5}


class FakeShopify:
    def graphql(self, query, variables=None):
        return {"shop": {"name": "demo", "currencyCode": "USD"}}

    def close(self):
        pass


def make_client(database_url, mapper):
    settings = Settings(
        database_url=database_url,
        ops_api_key="test-key",
        shopify_store="demo.myshopify.com",
        shopify_client_id="id",
        shopify_client_secret="secret",
    )
    return TestClient(create_app(settings, shopify=FakeShopify(), mapper=mapper))


@pytest.fixture
def client(database_url):
    with make_client(database_url, lambda table: (GOOD_MAPPING, CALL)) as test_client:
        yield test_client


def test_health_checks_the_database(client):
    assert client.get("/health").json() == {"ok": True, "db": True}


def test_routes_need_the_api_key(client):
    assert client.get("/shopify/shop").status_code == 401
    assert client.get("/shopify/shop", headers={"x-api-key": "wrong"}).status_code == 401
    assert client.post("/mappings", json={"table": "test_items"}).status_code == 401
    assert client.get("/shopify/shop", headers=KEY).json()["shop"]["name"] == "demo"


def test_propose_review_and_approve(client):
    response = client.post("/mappings", json={"table": "test_items"}, headers=KEY)
    assert response.status_code == 200
    proposal = response.json()
    assert proposal["status"] == "proposed"
    assert proposal["problems"] == []
    assert proposal["needs_review"] == ["activo"]
    assert proposal["preview"][0]["shopify"] == {
        "sku": "A-1",
        "title": "Tres Leches Porcion",
        "price": "4.50",
        "status": "ACTIVE",
    }
    assert proposal["preview"][2]["excluded"] is True

    mapping_id = proposal["id"]
    assert client.get(f"/mappings/{mapping_id}", headers=KEY).json()["status"] == "proposed"
    assert client.post(f"/mappings/{mapping_id}/approve", headers=KEY).json()["status"] == "approved"
    assert client.post(f"/mappings/{mapping_id}/reject", headers=KEY).status_code == 409


def test_only_pos_tables_can_be_read(client):
    assert client.post("/mappings", json={"table": "customers"}, headers=KEY).status_code == 404


def test_a_broken_mapping_cannot_be_approved(database_url):
    broken = GOOD_MAPPING.model_copy(update={"fields": GOOD_MAPPING.fields[:2]})
    with make_client(database_url, lambda table: (broken, CALL)) as client:
        proposal = client.post("/mappings", json={"table": "test_items"}, headers=KEY).json()
        assert "Shopify field 'price' is required but not mapped" in proposal["problems"]
        assert client.post(f"/mappings/{proposal['id']}/approve", headers=KEY).status_code == 422


def test_claude_failures_are_logged_and_reported(database_url):
    def failing(table):
        raise MappingFailed("Claude stopped with 'max_tokens'", {"model": "fake", "output_tokens": 8000})

    with make_client(database_url, failing) as client:
        assert client.post("/mappings", json={"table": "test_items"}, headers=KEY).status_code == 502
        with client.app.state.service.pool.connection() as conn:
            outcome = conn.execute(
                "SELECT outcome FROM llm_calls WHERE route = 'sync/mapping' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert outcome == ("error",)
