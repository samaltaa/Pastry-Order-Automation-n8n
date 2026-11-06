from app.mapping import Mapping

KEY = {"x-api-key": "test-key"}

PROPOSAL = Mapping.model_validate(
    {
        "key_column": "codigo",
        "fields": [
            {
                "source_column": "codigo",
                "target": "sku",
                "transform": "trim_upper",
                "confidence": 0.9,
                "reason": "-",
            },
            {
                "source_column": "descripcion",
                "target": "title",
                "transform": "none",
                "confidence": 0.9,
                "reason": "-",
            },
            {
                "source_column": "precio",
                "target": "price",
                "transform": "money",
                "confidence": 0.9,
                "reason": "-",
            },
            {
                "source_column": "existencia",
                "target": "quantity",
                "transform": "integer",
                "confidence": 0.5,
                "reason": "-",
            },
            {
                "source_column": "activo",
                "target": "ignore",
                "transform": "none",
                "confidence": 0.9,
                "reason": "-",
            },
            {
                "source_column": "foto",
                "target": "ignore",
                "transform": "none",
                "confidence": 0.9,
                "reason": "-",
            },
        ],
        "excluded_rows": [{"key": "SYS", "reason": "-"}],
    }
)
CALL = {"model": "fake", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0, "latency_ms": 1}


def say(client, text):
    response = client.post("/commands", json={"text": text}, headers=KEY)
    assert response.status_code == 200
    return response.json()["reply"]


def test_sync_command_reports_what_changed(db, make_client):
    with make_client() as client:
        reply = say(client, "/sync")
    assert reply.startswith("Sync run 1: 2 created, 0 updated, 0 stock changes, 0 unchanged.")
    assert "1 failed, 0 stock conflicts:" in reply
    assert "- A-3: Missing price" in reply


def test_mapping_then_approve_from_telegram(db, make_client):
    with make_client(mapper=lambda table: (PROPOSAL, CALL)) as client:
        reply = say(client, "/mapping")
        assert reply.splitlines()[0] == "Mapping 2 for pos.sync_test:"
        assert "- existencia -> quantity (integer) (check this)" in reply
        assert "- activo ignored" in reply
        assert "Skipped rows: SYS" in reply
        assert reply.splitlines()[-1] == "Reply /approve 2 or /reject 2"

        assert say(client, "/approve 2") == "Mapping 2 approved."
        assert say(client, "/approve 2") == "Couldn't do that: Mapping 2 is already approved"
        assert say(client, "/reject") == "Which mapping? For example: /reject 3"


def test_runs_and_help(db, make_client):
    with make_client() as client:
        assert say(client, "/runs") == "No sync runs yet."
        say(client, "/sync")
        assert say(client, "/runs").startswith("#1 ")
        assert say(client, "/whatever").startswith("Commands:")
        assert say(client, "/sync@PastryOpsBot").startswith("Sync run 2:")
