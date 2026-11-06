import pytest

from app.demo import RateLimiter, parse_csv
from app.mapping import Mapping

PREVIEW = Mapping.model_validate(
    {
        "key_column": "SKU",
        "fields": [
            {"source_column": "SKU", "target": "sku", "transform": "trim", "confidence": 0.9, "reason": "-"},
            {
                "source_column": "Name",
                "target": "title",
                "transform": "none",
                "confidence": 0.9,
                "reason": "-",
            },
            {
                "source_column": "Price",
                "target": "price",
                "transform": "money",
                "confidence": 0.9,
                "reason": "-",
            },
        ],
        "excluded_rows": [],
    }
)
CALL = {"model": "fake", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0, "latency_ms": 1}


@pytest.fixture
def client(db, make_client):
    with make_client(
        mapper=lambda table: (PREVIEW, CALL),
        demo_enabled=True,
        storefront_url="https://demo.myshopify.com",
    ) as test_client:
        yield test_client


def test_parse_csv_handles_semicolons_short_rows_and_blank_lines():
    table = parse_csv("SKU;Name;Price\nA-1;Flan;$4.50\n\nA-2;Pan\n")
    assert [column["name"] for column in table["columns"]] == ["SKU", "Name", "Price"]
    assert table["rows"] == [
        {"SKU": "A-1", "Name": "Flan", "Price": "$4.50"},
        {"SKU": "A-2", "Name": "Pan", "Price": None},
    ]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("SKU,Name\n", "header row and at least one product row"),
        ("SKU,SKU\nA,B\n", "unique, non-empty name"),
        (",Name\nA,B\n", "unique, non-empty name"),
    ],
)
def test_parse_csv_rejects_files_it_cannot_map(content, message):
    with pytest.raises(ValueError, match=message):
        parse_csv(content)


def test_rate_limiter_allows_a_burst_then_refuses():
    limiter = RateLimiter()
    assert [limiter.allow("visitor", 2, 60) for _ in range(3)] == [True, True, False]
    assert limiter.allow("someone else", 2, 60)


def test_demo_is_off_unless_enabled(db, make_client):
    with make_client() as client:
        assert client.get("/demo").status_code == 404


def test_page_and_state(client):
    assert "kept in sync with its Shopify store" in client.get("/demo").text
    state = client.get("/demo/api/state").json()
    assert state["table"] == "pos.sync_test"
    assert state["storefront"]["url"] == "https://demo.myshopify.com"
    first = state["products"][0]
    assert (first["sku"], first["title"], first["price"], first["handle"], first["shopify"]) == (
        "A-1",
        "Tres Leches Porcion",
        "4.50",
        "a-1",
        None,
    )


def test_edits_go_out_with_sync_now(client, store, db):
    client.post("/demo/api/sync")
    edits = [{"key": "a-1 ", "price": 5.25, "quantity": 7}, {"key": "A-2", "quantity": 4}]
    report = client.post("/demo/api/sync", json={"edits": edits}).json()

    assert report["counts"]["updated"] == 1 and report["counts"]["stock_updated"] == 2
    assert store.by_sku("A-1")["price"] == "5.25"
    assert db.execute("SELECT precio, existencia FROM pos.sync_test WHERE codigo = 'a-1 '").fetchone() == (
        "5.25",
        7,
    )
    assert db.execute("SELECT trigger FROM sync.runs ORDER BY id DESC LIMIT 1").fetchone() == ("demo",)


def test_edits_are_limited_to_safe_values_and_real_products(client, db):
    def sync_with(edit):
        return client.post("/demo/api/sync", json={"edits": [edit]}).status_code

    assert sync_with({"key": "A-2", "price": 9999}) == 422
    assert sync_with({"key": "A-2", "quantity": -1}) == 422
    assert sync_with({"key": "NOPE", "price": 3}) == 404
    assert db.execute("SELECT COUNT(*) FROM sync.runs").fetchone() == (0,)


def test_a_bad_edit_saves_nothing(client, db):
    edits = [{"key": "A-2", "price": 7}, {"key": "NOPE", "price": 3}]
    assert client.post("/demo/api/sync", json={"edits": edits}).status_code == 404
    assert db.execute("SELECT precio FROM pos.sync_test WHERE codigo = 'A-2'").fetchone() == ("19",)


def test_csv_preview_is_shown_but_not_saved(client, db):
    response = client.post("/demo/api/csv", json={"content": "SKU,Name,Price\nX-1,Cake,$30\n"})
    report = response.json()
    assert (report["id"], report["status"], report["problems"]) == (None, "preview", [])
    assert report["preview"][0]["shopify"] == {"sku": "X-1", "title": "Cake", "price": "30.00"}
    assert db.execute("SELECT COUNT(*) FROM sync.mappings").fetchone() == (1,)


def test_csv_uploads_are_rate_limited(client):
    body = {"content": "SKU,Name,Price\nX-1,Cake,$30\n"}
    statuses = [client.post("/demo/api/csv", json=body).status_code for _ in range(4)]
    assert statuses == [200, 200, 200, 429]


def test_demo_explains_a_missing_mapping(client, db):
    db.execute("UPDATE sync.mappings SET status = 'rejected'")
    response = client.get("/demo/api/state")
    assert response.status_code == 409
    assert "No approved mapping yet" in response.json()["detail"]
