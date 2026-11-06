"""What the service can do, in one place. The HTTP API, the Telegram commands and the demo page
all call these methods, so the rules (budget, approval checks, locking) can't drift apart."""

from collections.abc import Callable

import anthropic
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app import mapping as mappings
from app.config import Settings
from app.llm import TIMEZONE, MappingFailed, log_call, spent_today
from app.pos import UnknownTable, describe_table
from app.shopify import ShopifyClient
from app.sync import NoApprovedMapping, SyncAlreadyRunning, run_sync

PREVIEW_ROWS = 5

Mapper = Callable[[dict], tuple[mappings.Mapping, dict]]


class Problem(Exception):
    """An expected failure with the HTTP status that describes it."""

    def __init__(self, status: int, detail):
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


class Service:
    def __init__(self, settings: Settings, pool: ConnectionPool, shopify: ShopifyClient, mapper: Mapper):
        self.settings = settings
        self.pool = pool
        self.shopify = shopify
        self.mapper = mapper

    def table(self, name: str) -> dict:
        with self.pool.connection() as conn:
            try:
                return describe_table(conn, name)
            except UnknownTable as error:
                raise Problem(404, str(error)) from error

    def report(self, mapping_id: int | None, status: str, mapping: mappings.Mapping, table: dict) -> dict:
        """What a reviewer needs to decide: the mapping, what's wrong, what's unsure, and sample output."""
        columns = [column["name"] for column in table["columns"]]
        return {
            "id": mapping_id,
            "status": status,
            "source_table": table["table"],
            "mapping": mapping.model_dump(),
            "problems": mappings.problems(mapping, columns),
            "needs_review": [field.source_column for field in mappings.needs_review(mapping)],
            "preview": [
                {
                    "key": row.get(mapping.key_column),
                    "excluded": mappings.is_excluded(mapping, row),
                    "shopify": mappings.apply(mapping, row),
                }
                for row in table["rows"][:PREVIEW_ROWS]
            ],
        }

    def ask_claude(self, table: dict) -> tuple[mappings.Mapping, str]:
        """One mapping proposal, within the daily budget. Every call is logged, failed or not."""
        with self.pool.connection() as conn:
            if spent_today(conn) >= self.settings.daily_llm_budget_usd:
                raise Problem(429, "Daily AI budget reached")
        try:
            mapping, call = self.mapper(table)
        except Exception as error:
            failed = getattr(error, "call", {}) or {"model": self.settings.anthropic_model_strong}
            with self.pool.connection() as conn:
                log_call(conn, table["table"], {**failed, "outcome": "error", "error": str(error)[:500]})
            status = 502 if isinstance(error, (MappingFailed, anthropic.APIError)) else 500
            raise Problem(status, "Claude could not propose a mapping") from error
        with self.pool.connection() as conn:
            log_call(conn, table["table"], {**call, "outcome": "ok"})
        return mapping, call["model"]

    def propose(self, table_name: str) -> dict:
        table = self.table(table_name)
        mapping, model = self.ask_claude(table)
        with self.pool.connection() as conn:
            mapping_id = conn.execute(
                "INSERT INTO sync.mappings (source_table, mapping, model) VALUES (%s, %s, %s) RETURNING id",
                (table["table"], Jsonb(mapping.model_dump()), model),
            ).fetchone()[0]
        return self.report(mapping_id, "proposed", mapping, table)

    def preview_only(self, table: dict) -> dict:
        """A mapping for data that isn't stored (an uploaded CSV). Nothing is saved or synced."""
        mapping, _ = self.ask_claude(table)
        return self.report(None, "preview", mapping, table)

    def load(self, mapping_id: int) -> tuple[str, str, mappings.Mapping]:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT source_table, status, mapping FROM sync.mappings WHERE id = %s", (mapping_id,)
            ).fetchone()
        if not row:
            raise Problem(404, f"No mapping {mapping_id}")
        return row[0], row[1], mappings.Mapping.model_validate(row[2])

    def show(self, mapping_id: int) -> dict:
        source_table, status, mapping = self.load(mapping_id)
        return self.report(mapping_id, status, mapping, self.table(source_table.split(".", 1)[1]))

    def review(self, mapping_id: int, decision: str) -> dict:
        if decision not in ("approve", "reject"):
            raise Problem(404, "Use approve or reject")
        source_table, status, mapping = self.load(mapping_id)
        if status != "proposed":
            raise Problem(409, f"Mapping {mapping_id} is already {status}")
        if decision == "approve":
            table = self.table(source_table.split(".", 1)[1])
            found = mappings.problems(mapping, [column["name"] for column in table["columns"]])
            if found:
                raise Problem(422, found)

        new_status = "approved" if decision == "approve" else "rejected"
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE sync.mappings SET status = %s, reviewed_at = NOW() WHERE id = %s",
                (new_status, mapping_id),
            )
        return {"id": mapping_id, "status": new_status}

    def sync(self, table: str, dry_run: bool, trigger: str) -> dict:
        with self.pool.connection() as conn:
            try:
                return run_sync(
                    conn, self.shopify, table, dry_run, trigger, self.settings.shopify_location_id
                )
            except (NoApprovedMapping, SyncAlreadyRunning) as error:
                raise Problem(409, str(error)) from error

    def runs(self, limit: int = 10) -> list[dict]:
        with self.pool.connection() as conn:
            return (
                conn.cursor(row_factory=dict_row)
                .execute(
                    """
                    SELECT *, to_char(started_at AT TIME ZONE %s, 'Mon DD HH24:MI') AS started_local
                    FROM sync.runs ORDER BY id DESC LIMIT %s
                    """,
                    (TIMEZONE, min(limit, 100)),
                )
                .fetchall()
            )
