"""What the service can do, in one place. The HTTP API, the Telegram commands and the demo page
all call these methods, so the rules (budget, approval checks, locking) can't drift apart."""

import logging
import time
from collections.abc import Callable

import anthropic
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app import mapping as mappings
from app.config import Settings
from app.llm import TIMEZONE, MappingFailed, log_call, spent_today
from app.shopify import ShopifyClient
from app.sources import BadSheet, UnknownTable, describe_table, full_name, qualify, store_sheet
from app.sync import NoApprovedMapping, SyncAlreadyRunning, approved_mapping, run_sync

PREVIEW_ROWS = 5
STOCK_WEBHOOK_TOPIC = "INVENTORY_LEVELS_UPDATE"

FIND_WEBHOOKS = """
query Webhooks($topics: [WebhookSubscriptionTopic!]) {
  webhookSubscriptions(first: 10, topics: $topics) { nodes { id uri } }
}
"""

CREATE_WEBHOOK = """
mutation CreateWebhook($topic: WebhookSubscriptionTopic!, $webhook: WebhookSubscriptionInput!) {
  webhookSubscriptionCreate(topic: $topic, webhookSubscription: $webhook) {
    webhookSubscription { id uri }
    userErrors { field message }
  }
}
"""

UPDATE_WEBHOOK = """
mutation UpdateWebhook($id: ID!, $webhook: WebhookSubscriptionInput!) {
  webhookSubscriptionUpdate(id: $id, webhookSubscription: $webhook) {
    webhookSubscription { id uri }
    userErrors { field message }
  }
}
"""
LOCK_RETRIES = 10
INGEST_LOCK = 7_270_003

log = logging.getLogger(__name__)

Mapper = Callable[[dict], tuple[mappings.Mapping, dict]]
Post = Callable[[str, dict], None]


class Problem(Exception):
    """An expected failure with the HTTP status that describes it."""

    def __init__(self, status: int, detail):
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


class Service:
    def __init__(
        self, settings: Settings, pool: ConnectionPool, shopify: ShopifyClient, mapper: Mapper, post: Post
    ):
        self.settings = settings
        self.pool = pool
        self.shopify = shopify
        self.mapper = mapper
        self.post = post  # JSON POST to another service (Telegram, Make); tests pass a recorder
        self.source = full_name(settings.sync_source)
        self.on_sheet = qualify(self.source)[0] == "sheets"

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
        return self.report(mapping_id, status, mapping, self.table(source_table))

    def review(self, mapping_id: int, decision: str) -> dict:
        if decision not in ("approve", "reject"):
            raise Problem(404, "Use approve or reject")
        source_table, status, mapping = self.load(mapping_id)
        if status != "proposed":
            raise Problem(409, f"Mapping {mapping_id} is already {status}")
        if decision == "approve":
            table = self.table(source_table)
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

    def sync(self, table: str | None, dry_run: bool, trigger: str, wait: bool = False) -> dict:
        """One sync run of a table, or of the active source. With wait, a run already in progress
        is waited out (up to about 30 seconds) instead of refused."""
        for attempt in range(LOCK_RETRIES if wait else 1):
            if attempt:
                time.sleep(3)
            with self.pool.connection() as conn:
                try:
                    return run_sync(
                        conn,
                        self.shopify,
                        table or self.source,
                        dry_run,
                        trigger,
                        self.settings.shopify_location_id,
                    )
                except NoApprovedMapping as error:
                    raise Problem(409, str(error)) from error
                except SyncAlreadyRunning as error:
                    busy = error
        raise Problem(409, str(busy))

    def ingest_sheet(self, name: str, values: list[list]) -> dict:
        """Stores a spreadsheet snapshot from Make, then syncs it if it's the active source.

        If the columns don't match the approved mapping (first snapshot, or someone renamed or
        added a column), nothing is synced: Claude proposes a mapping and the owner approves it.
        """
        # One sheet arrival at a time: a single edit can make Make send the sheet twice, and two
        # arrivals deciding at once would both ask Claude and both message the owner.
        with self.pool.connection() as guard:
            guard.execute("SELECT pg_advisory_lock(%s)", (INGEST_LOCK,))
            try:
                return self._ingest_sheet(name, values)
            finally:
                guard.execute("SELECT pg_advisory_unlock(%s)", (INGEST_LOCK,))

    def _ingest_sheet(self, name: str, values: list[list]) -> dict:
        source = f"sheets.{name}"
        with self.pool.connection() as conn:
            try:
                columns = store_sheet(conn, name, values)
            except BadSheet as error:
                raise Problem(422, str(error)) from error
            try:
                _, approved = approved_mapping(conn, source)
            except NoApprovedMapping:
                approved = None

        if source != self.source:
            return {"status": "stored", "detail": f"Saved, not synced: the active source is {self.source}"}
        if approved and not mappings.problems(approved, columns):
            return {"status": "synced", "sync": self.sync(source, dry_run=False, trigger="sheet", wait=True)}

        pending = self.pending_mapping(source, columns)
        if pending:
            return {"status": "needs_approval", "mapping_id": pending, "new_proposal": None}
        proposal = self.propose(source)
        proposal["first"] = approved is None
        return {"status": "needs_approval", "mapping_id": proposal["id"], "new_proposal": proposal}

    def pending_mapping(self, source: str, columns: list[str]) -> int | None:
        """A proposal still waiting for approval that fits these columns, so Claude isn't asked again."""
        with self.pool.connection() as conn:
            row = conn.execute(
                """
                SELECT id, mapping FROM sync.mappings
                WHERE source_table = %s AND status = 'proposed' ORDER BY id DESC LIMIT 1
                """,
                (source,),
            ).fetchone()
        if row and not mappings.problems(mappings.Mapping.model_validate(row[1]), columns):
            return row[0]
        return None

    def write_to_sheet(self, key: str, cells: dict) -> bool:
        """Sends one row's price and stock to Make, which finds the row by SKU and updates it."""
        try:
            self.post(self.settings.make_sheet_webhook_url, {"sku": key, **cells})
            return True
        except Exception as error:
            log.warning("Writing %s to the sheet failed: %s", key, error)
            return False

    def first_delivery(self, delivery_id: str, topic: str) -> bool:
        """False when this Shopify webhook delivery was already handled (Shopify retries)."""
        with self.pool.connection() as conn:
            return (
                conn.execute(
                    "INSERT INTO sync.webhook_deliveries (id, topic) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (delivery_id, topic),
                ).rowcount
                == 1
            )

    def shopify_stock_changed(self, inventory_item_id: str, available: int) -> dict | None:
        """Shopify's stock moved: an online sale, a cancellation, or staff editing it in the admin.

        The source takes Shopify's number, and so does sync.items, so the next sync neither reports
        a conflict nor puts the old count back. Shopify also sends this after the sync's own stock
        writes; those already match sync.items and are ignored. Returns the row to write back to a
        sheet source, or None.
        """
        with self.pool.connection() as conn:
            item = conn.execute(
                "SELECT sku, source_key, last_quantity FROM sync.items WHERE shopify_inventory_item_id = %s",
                (inventory_item_id,),
            ).fetchone()
            if not item or item[2] == available:
                return None
            sku, source_key, _ = item
            try:
                _, mapping = approved_mapping(conn, self.source)
            except NoApprovedMapping:
                return None
            columns = {field.target: field.source_column for field in mapping.fields}
            if "quantity" not in columns:
                return None
            row = conn.execute(
                sql.SQL("UPDATE {} SET {} = %s WHERE trim({}) = %s RETURNING {}").format(
                    sql.Identifier(*qualify(self.source)),
                    sql.Identifier(columns["quantity"]),
                    sql.Identifier(mapping.key_column),
                    sql.Identifier(columns["price"]),
                ),
                (str(available), source_key),
            ).fetchone()
            conn.execute("UPDATE sync.items SET last_quantity = %s WHERE sku = %s", (available, sku))
        log.info("Shopify stock for %s is now %s; source updated", sku, available)
        if row is None or not (self.on_sheet and self.settings.make_sheet_webhook_url):
            return None
        return {"key": source_key, "cells": {"price": row[0], "quantity": str(available)}}

    def register_stock_webhook(self, url: str) -> dict:
        """Points Shopify's stock-change webhook at this service. Run again whenever the address changes."""
        found = self.shopify.graphql(FIND_WEBHOOKS, {"topics": [STOCK_WEBHOOK_TOPIC]})
        existing = found["webhookSubscriptions"]["nodes"]
        webhook = {"uri": url, "format": "JSON"}
        if existing:
            result = self.shopify.graphql(UPDATE_WEBHOOK, {"id": existing[0]["id"], "webhook": webhook})
            result = result["webhookSubscriptionUpdate"]
        else:
            result = self.shopify.graphql(CREATE_WEBHOOK, {"topic": STOCK_WEBHOOK_TOPIC, "webhook": webhook})
            result = result["webhookSubscriptionCreate"]
        if result["userErrors"]:
            raise Problem(502, result["userErrors"])
        return result["webhookSubscription"]

    def notify_owner(self, text: str) -> None:
        """Telegram message to the owner. A failed alert never fails the request that raised it."""
        token, chat = self.settings.telegram_bot_token, self.settings.owner_telegram_chat_id
        if not (token and chat):
            return
        try:
            self.post(f"https://api.telegram.org/bot{token}/sendMessage", {"chat_id": chat, "text": text})
        except Exception as error:
            # Only the error type: the URL holds the bot token
            log.warning("Telegram alert failed: %s", type(error).__name__)

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
