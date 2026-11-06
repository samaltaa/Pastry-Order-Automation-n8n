import pytest

KEY = {"x-api-key": "test-key"}


@pytest.fixture
def client(make_client):
    with make_client() as test_client:
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
        "published": 2,
        "unchanged": 0,
        "excluded": 1,
        "failed": 1,
        "conflicts": 0,
        "removed": 0,
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
    assert change["detail"] == "Shopify had 15, expected 18; source value 10 applied"
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


def test_products_are_published_to_the_online_store_once(db, client, store):
    run(client)
    assert store.published == {store.by_sku("A-1")["id"], store.by_sku("A-2")["id"]}

    db.execute("UPDATE sync.items SET published = FALSE WHERE sku = 'A-2'")
    store.published.clear()
    report = run(client)
    assert report["counts"]["published"] == 1
    assert {"sku": "A-2", "title": "Flan de Coco", "action": "publish"} in report["changes"]
    assert store.published == {store.by_sku("A-2")["id"]}
    assert run(client)["counts"]["published"] == 0


def test_images_are_sent_on_create_and_only_again_when_they_change(db, client, store):
    run(client)
    assert store.by_sku("A-1")["images"] == ["https://img.test/tres-leches.jpg"]
    assert "images" not in store.by_sku("A-2")

    db.execute("UPDATE pos.sync_test SET precio = '$4.75' WHERE codigo = 'a-1 '")
    store.writes.clear()
    run(client)
    assert store.writes == ["productSet A-1"]

    db.execute("UPDATE pos.sync_test SET foto = 'https://img.test/new.jpg' WHERE codigo = 'a-1 '")
    run(client)
    assert store.by_sku("A-1")["images"] == ["https://img.test/new.jpg"]

    db.execute("UPDATE pos.sync_test SET foto = NULL WHERE codigo = 'a-1 '")
    run(client)
    assert store.by_sku("A-1")["images"] == []
