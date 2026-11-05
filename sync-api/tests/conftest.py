import os
from pathlib import Path

import psycopg
import pytest

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
