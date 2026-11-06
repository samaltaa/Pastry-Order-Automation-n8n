"""What the service can do, in one place. The HTTP API, the Telegram commands and the demo page
all call these methods, so the rules (budget, approval checks, locking) can't drift apart."""

import logging
import time
from collections.abc import Callable
from decimal import Decimal

import anthropic
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app import gate
from app import mapping as mappings
from app.config import Settings
from app.llm import TIMEZONE, MappingFailed, log_call, spent_today
from app.shopify import ShopifyClient
from app.sources import BadSheet, UnknownTable, describe_table, full_name, qualify, read_rows, store_sheet
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
Reviewer = Callable[[list[dict]], tuple[list[dict], dict]]


class Problem(Exception):
    """An expected failure with the HTTP status that describes it."""

    def __init__(self, status: int, detail):
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


class Service:
    def __init__(
        self,
        settings: Settings,
        pool: ConnectionPool,
        shopify: ShopifyClient,
        mapper: Mapper,
        post: Post,
        reviewer: Reviewer | None = None,
    ):
        self.settings = settings
        self.pool = pool
        self.shopify = shopify
        self.mapper = mapper
        self.reviewer = reviewer
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
        """Claude's proposal, stored with the deterministic checks it was judged on."""
        table = self.table(table_name)
        mapping, model = self.ask_claude(table)
        checks = self.checks(table["table"], mapping)
        with self.pool.connection() as conn:
            mapping_id = conn.execute(
                "INSERT INTO sync.mappings (source_table, mapping, model, checks)"
                " VALUES (%s, %s, %s, %s) RETURNING id",
                (table["table"], Jsonb(mapping.model_dump()), model, Jsonb(checks)),
            ).fetchone()[0]
        return {**self.report(mapping_id, "proposed", mapping, table), "checks": checks}

    def checks(self, source: str, mapping: mappings.Mapping) -> list[dict]:
        """The approval checks (app/gate.py), run on every row of the source and on what Shopify has."""
        with self.pool.connection() as conn:
            rows = read_rows(conn, source)
            columns = list(rows[0]) if rows else []
            try:
                _, previous = approved_mapping(conn, source)
            except NoApprovedMapping:
                previous = None
            known = dict(conn.execute("SELECT sku, data_hash FROM sync.items").fetchall())
        return gate.check(mapping, columns, rows, previous, known)

    def approve(self, mapping_id: int, approved_by: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE sync.mappings SET status = 'approved', approved_by = %s, reviewed_at = NOW()"
                " WHERE id = %s",
                (approved_by, mapping_id),
            )

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

        if decision == "approve":
            self.approve(mapping_id, "owner")
            return {"id": mapping_id, "status": "approved"}
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE sync.mappings SET status = 'rejected', reviewed_at = NOW() WHERE id = %s",
                (mapping_id,),
            )
        return {"id": mapping_id, "status": "rejected"}

    def sync(self, table: str | None, dry_run: bool, trigger: str, wait: bool = False) -> dict:
        """One sync run of a table, or of the active source. With wait, a run already in progress
        is waited out (up to about 30 seconds) instead of refused. Afterwards, products that just
        ran low are reported to the owner."""
        busy = None
        for attempt in range(LOCK_RETRIES if wait else 1):
            if attempt:
                time.sleep(3)
            with self.pool.connection() as conn:
                try:
                    report = run_sync(
                        conn,
                        self.shopify,
                        table or self.source,
                        dry_run,
                        trigger,
                        self.settings.shopify_location_id,
                        self.review_changes,
                    )
                except NoApprovedMapping as error:
                    raise Problem(409, str(error)) from error
                except SyncAlreadyRunning as error:
                    busy = error
                    continue
            if not dry_run:
                self.alert_low_stock()
                self.alert_held(report["changes"])
            return report
        raise Problem(409, str(busy))

    def review_changes(self, changes: list[dict]) -> list[dict]:
        """Claude's flags on these changes. Over budget or failed, there are none: a sync never
        waits on Claude, and the rules in app/guard.py still apply."""
        if not self.reviewer:
            return []
        with self.pool.connection() as conn:
            if spent_today(conn) >= self.settings.daily_llm_budget_usd:
                log.warning("Daily AI budget reached: changes weren't reviewed")
                return []
        try:
            flags, call = self.reviewer(changes)
        except Exception as error:
            log.warning("Reviewing changes failed: %s", type(error).__name__)
            with self.pool.connection() as conn:
                failed = getattr(error, "call", {}) or {"model": self.settings.anthropic_model_fast}
                log_call(
                    conn, "changes", {**failed, "outcome": "error", "error": str(error)[:500]}, "sync/review"
                )
            return []
        with self.pool.connection() as conn:
            log_call(conn, "changes", {**call, "outcome": "ok"}, "sync/review")
        return flags

    def review_stock_move(self, sku: str, old: int, new: int) -> None:
        """A sale has already happened, so it can't be held. If Claude finds it unusual, it's listed
        for approval, and declining puts the stock back."""
        with self.pool.connection() as conn:
            try:
                _, mapping = approved_mapping(conn, self.source)
            except NoApprovedMapping:
                return
            products = (mappings.apply(mapping, row) for row in read_rows(conn, self.source))
            product = next((product for product in products if product.get("sku") == sku), {})
        change = {
            "sku": sku,
            "product": product.get("title"),
            "category": product.get("product_type"),
            "field": "quantity",
            "old": str(old),
            "new": str(new),
            "from": "Shopify: an online sale or an admin edit",
        }
        flags = self.review_changes([change])
        if not flags:
            return
        with self.pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO sync.held_changes
                  (sku, field, old_value, new_value, reason, flagged_by, applied)
                VALUES (%s, 'quantity', %s, %s, %s, 'claude', TRUE)
                ON CONFLICT (sku, field) DO UPDATE SET new_value = EXCLUDED.new_value,
                  old_value = CASE WHEN sync.held_changes.applied THEN sync.held_changes.old_value
                                   ELSE EXCLUDED.old_value END,
                  reason = EXCLUDED.reason, flagged_by = 'claude', applied = TRUE, released = FALSE,
                  held_at = NOW()
                """,
                (sku, str(old), str(new), flags[0]["reason"]),
            )
        self.notify_owner(
            f"Unusual stock change for {product.get('title') or sku} ({sku}): {old} -> {new}. "
            f"{flags[0]['reason']} Approve or decline it on the demo page."
        )

    def held_changes(self) -> list[dict]:
        """Changes waiting for a decision, newest first."""
        with self.pool.connection() as conn:
            return (
                conn.cursor(row_factory=dict_row)
                .execute(
                    """
                    SELECT sku, field, old_value, new_value, reason, released, flagged_by, applied,
                           to_char(held_at AT TIME ZONE %s, 'Mon DD HH24:MI') AS held_local
                    FROM sync.held_changes ORDER BY held_at DESC
                    """,
                    (TIMEZONE,),
                )
                .fetchall()
            )

    def release(self, sku: str, field: str | None = None) -> dict:
        """A flagged change was intended: it's applied by a sync straight away, or, if it already
        happened, simply kept. Without a field, everything flagged for the product is released."""
        with self.pool.connection() as conn:
            fields = [
                row[0]
                for row in conn.execute(
                    "UPDATE sync.held_changes SET released = TRUE WHERE upper(sku) = upper(%s)"
                    " AND (%s::text IS NULL OR field = %s) RETURNING field",
                    (sku.strip(), field, field),
                )
            ]
            conn.execute("DELETE FROM sync.held_changes WHERE released AND applied")
        if not fields:
            raise Problem(404, f"Nothing is held for {sku}")
        return {
            "sku": sku.strip().upper(),
            "released": fields,
            "sync": self.sync(None, False, "manual", wait=True),
        }

    def decline(self, sku: str, field: str) -> dict:
        """A flagged change was a mistake: the source goes back to the old value, the sheet too, and
        the sync takes Shopify back if the change had reached it."""
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT old_value FROM sync.held_changes WHERE sku = %s AND field = %s AND NOT released",
                (sku, field),
            ).fetchone()
            if not row:
                raise Problem(404, f"Nothing is held for {sku}")
            if row[0] is None:
                raise Problem(409, "This is a new product with no old value: correct it in the source.")
            key = conn.execute("SELECT source_key FROM sync.items WHERE sku = %s", (sku,)).fetchone()
            if not key:
                raise Problem(409, "This product isn't in Shopify yet: correct it in the source.")
            value = f"${Decimal(row[0]):.2f}" if field == "price" and self.on_sheet else row[0]
            _, mapping = approved_mapping(conn, self.source)
            cells = self.set_cell(conn, mapping, key[0], field, value)
            # Released at its old value, so neither the rules nor Claude stop it going back
            conn.execute(
                "UPDATE sync.held_changes SET new_value = old_value, released = TRUE, applied = FALSE"
                " WHERE sku = %s AND field = %s",
                (sku, field),
            )
        if cells and self.on_sheet and self.settings.make_sheet_webhook_url:
            self.write_to_sheet(key[0], cells)
        return {"sku": sku, "declined": field, "sync": self.sync(None, False, "demo", wait=True)}

    def set_cell(self, conn, mapping: mappings.Mapping, key: str, target: str, value: str) -> dict | None:
        """Writes one value to a source row. Returns the row's price and stock cells."""
        columns = {field.target: field.source_column for field in mapping.fields}
        row = conn.execute(
            sql.SQL("UPDATE {} SET {} = %s WHERE trim({}) = %s RETURNING {}, {}").format(
                sql.Identifier(*qualify(self.source)),
                sql.Identifier(columns[target]),
                sql.Identifier(mapping.key_column),
                sql.Identifier(columns["price"]),
                sql.Identifier(columns["quantity"]),
            ),
            (value, key),
        ).fetchone()
        return {"price": row[0], "quantity": row[1]} if row else None

    def alert_held(self, changes: list[dict]) -> None:
        new = [change for change in changes if change["action"] == "held" and change.get("new")]
        if new:
            lines = [f"- {change['title']} ({change['sku']}): {change['detail']}" for change in new]
            skus = sorted({change["sku"] for change in new})
            self.notify_owner(
                "Held for review (looks like a typo; the store kept the old value):\n"
                + "\n".join(lines)
                + "\nIf it was intended: "
                + ", ".join(f"/release {sku}" for sku in skus)
            )

    def low_stock(self) -> list[dict]:
        """Products for sale whose stock is at or below their reorder level, the emptiest first."""
        with self.pool.connection() as conn:
            try:
                _, mapping = approved_mapping(conn, self.source)
            except NoApprovedMapping:
                return []
            if "reorder_level" not in {field.target for field in mapping.fields}:
                return []
            rows = read_rows(conn, self.source)
        low = []
        for row in rows:
            product = mappings.apply(mapping, row)
            level, quantity = _whole(product.get("reorder_level")), _whole(product.get("quantity"))
            if (
                mappings.is_excluded(mapping, row)
                or not product.get("sku")
                or product.get("status") == "DRAFT"
                or level is None
                or quantity is None
                or quantity > level
            ):
                continue
            low.append(
                {
                    "sku": product["sku"],
                    "title": product.get("title"),
                    "quantity": quantity,
                    "reorder_level": level,
                    "image": product.get("image"),
                }
            )
        return sorted(low, key=lambda product: (product["quantity"], product["sku"]))

    def alert_low_stock(self) -> list[str]:
        """Telegram message for products that just ran low. Each alerts once, and again only after
        it was restocked above its reorder level. Never fails the caller."""
        try:
            low = self.low_stock()
            with self.pool.connection() as conn:
                conn.execute(
                    "DELETE FROM sync.low_stock WHERE NOT (sku = ANY(%s))",
                    ([product["sku"] for product in low],),
                )
                new = [
                    product
                    for product in low
                    if conn.execute(
                        "INSERT INTO sync.low_stock (sku) VALUES (%s) ON CONFLICT DO NOTHING",
                        (product["sku"],),
                    ).rowcount
                ]
        except Exception as error:
            log.warning("Low-stock check failed: %s", error)
            return []
        if new:
            lines = [
                f"- {product['title']} ({product['sku']}): {product['quantity']} left, "
                f"reorder at {product['reorder_level']}"
                for product in new
            ]
            self.notify_owner("Running low:\n" + "\n".join(lines))
        return [product["sku"] for product in new]

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
        if self.settings.auto_approve_mappings and gate.passed(proposal["checks"]):
            self.approve(proposal["id"], "auto")
            report = self.sync(source, dry_run=False, trigger="sheet", wait=True)
            return {
                "status": "auto_approved",
                "mapping_id": proposal["id"],
                "sync": report,
                "new_proposal": proposal,
            }
        return {"status": "needs_approval", "mapping_id": proposal["id"], "new_proposal": proposal}

    def pending_mapping(self, source: str, columns: list[str]) -> int | None:
        """A proposal still waiting for approval that fits these columns, so Claude isn't asked again.
        Only proposals newer than the approved mapping count: older ones were overtaken."""
        with self.pool.connection() as conn:
            row = conn.execute(
                """
                SELECT id, mapping FROM sync.mappings
                WHERE source_table = %(source)s AND status = 'proposed'
                  AND id > COALESCE((SELECT MAX(id) FROM sync.mappings
                                     WHERE source_table = %(source)s AND status = 'approved'), 0)
                ORDER BY id DESC LIMIT 1
                """,
                {"source": source},
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
        writes; those already match sync.items and are ignored. Returns what moved, with the row
        to write back to a sheet source under "write", or None if nothing did.
        """
        with self.pool.connection() as conn:
            item = conn.execute(
                "SELECT sku, source_key, last_quantity FROM sync.items WHERE shopify_inventory_item_id = %s",
                (inventory_item_id,),
            ).fetchone()
            if not item or item[2] == available:
                return None
            sku, source_key, old = item
            try:
                _, mapping = approved_mapping(conn, self.source)
            except NoApprovedMapping:
                return None
            if "quantity" not in {field.target for field in mapping.fields}:
                return None
            cells = self.set_cell(conn, mapping, source_key, "quantity", str(available))
            conn.execute("UPDATE sync.items SET last_quantity = %s WHERE sku = %s", (available, sku))
        log.info("Shopify stock for %s is now %s; source updated", sku, available)
        writable = cells and self.on_sheet and self.settings.make_sheet_webhook_url
        return {
            "sku": sku,
            "old": old,
            "new": available,
            "write": {"key": source_key, "cells": cells} if writable else None,
        }

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


def _whole(value) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None
