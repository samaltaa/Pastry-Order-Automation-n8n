import os

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL not set")


class FakeShopify:
    def graphql(self, query, variables=None):
        return {"shop": {"name": "demo", "currencyCode": "USD"}}

    def close(self):
        pass


@pytest.fixture
def client():
    settings = Settings(
        database_url=DATABASE_URL,
        ops_api_key="test-key",
        shopify_store="demo.myshopify.com",
        shopify_client_id="id",
        shopify_client_secret="secret",
    )
    with TestClient(create_app(settings, shopify=FakeShopify())) as test_client:
        yield test_client


def test_health_checks_the_database(client):
    assert client.get("/health").json() == {"ok": True, "db": True}


def test_shopify_routes_need_the_api_key(client):
    assert client.get("/shopify/shop").status_code == 401
    assert client.get("/shopify/shop", headers={"x-api-key": "wrong"}).status_code == 401

    response = client.get("/shopify/shop", headers={"x-api-key": "test-key"})
    assert response.status_code == 200
    assert response.json()["shop"]["name"] == "demo"
