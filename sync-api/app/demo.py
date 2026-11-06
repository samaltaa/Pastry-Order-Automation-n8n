"""Public demo for reviewers: the inventory next to Shopify, edit a product, sync, try your own CSV.

Only mounted when DEMO_ENABLED=true. There's no API key here, so every action is rate-limited
per visitor, edits are limited to the price and stock of existing products, and CSV uploads
are previewed only: nothing from them is stored or sent to Shopify.

The inventory is the active source: the POS table, or the Google Sheet. When it's the sheet,
edits are also sent to Make, which writes them into the sheet, so the sheet never disagrees
with the store.
"""

import csv
import io
import logging
import threading
import time
from collections import defaultdict, deque
from itertools import islice
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from psycopg import sql
from pydantic import BaseModel, Field

from app.config import Settings
from app.mapping import apply, is_excluded
from app.service import Problem, Service
from app.sources import SAMPLE_ROWS, full_name, qualify, read_rows
from app.sync import NoApprovedMapping, approved_mapping, handle_for

log = logging.getLogger(__name__)

PAGE = Path(__file__).with_name("demo.html")
MAX_CSV_CHARS = 200_000
MAX_CSV_COLUMNS = 40


class RateLimiter:
    """Sliding window per key, kept in memory. Enough for one small server process."""

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, per_seconds: float) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > per_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True


class ProductEdit(BaseModel):
    key: str = Field(max_length=40)
    price: float | None = Field(default=None, ge=0.25, le=500)
    quantity: int | None = Field(default=None, ge=0, le=999)


class SyncNow(BaseModel):
    """Edits made on the page, saved to the inventory in one go just before the sync runs."""

    edits: list[ProductEdit] = Field(default_factory=list, max_length=50)


class CsvUpload(BaseModel):
    content: str = Field(max_length=MAX_CSV_CHARS)


def parse_csv(content: str) -> dict:
    """An uploaded CSV in the same shape as a POS table: columns plus sample rows as text."""
    # Spanish-language exports often use semicolons. The header line shows which separator it is.
    header_line = content.lstrip().split("\n", 1)[0]
    delimiter = max(",;\t|", key=header_line.count)
    reader = csv.reader(io.StringIO(content), delimiter=delimiter)
    rows = [row for row in islice(reader, SAMPLE_ROWS + 1) if any(row)]
    if len(rows) < 2:
        raise ValueError("The file needs a header row and at least one product row.")

    header = [name.strip() for name in rows[0]]
    if not all(header) or len(set(header)) != len(header):
        raise ValueError("Every column needs a unique, non-empty name in the header row.")
    if len(header) > MAX_CSV_COLUMNS:
        raise ValueError(f"Up to {MAX_CSV_COLUMNS} columns, please.")

    data = []
    for row in rows[1:]:
        values = [value.strip() or None for value in row[: len(header)]]
        data.append(dict(zip(header, values + [None] * (len(header) - len(values)), strict=True)))
    return {
        "table": "uploaded.csv",
        "columns": [{"name": name, "type": "text"} for name in header],
        "rows": data,
    }


def demo_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/demo")
    limiter = RateLimiter()
    page = PAGE.read_text()
    source = full_name(settings.sync_source)
    on_sheet = qualify(source)[0] == "sheets"
    sheet_url = settings.sheet_url if on_sheet else None

    def slow_down(request: Request, action: str, limit: int, per_seconds: int) -> None:
        visitor = request.client.host if request.client else "unknown"
        if not limiter.allow(f"{action}:{visitor}", limit, per_seconds):
            raise HTTPException(status_code=429, detail="That's a lot of clicks. Please wait a minute.")

    def service(request: Request) -> Service:
        return request.app.state.service

    def current_mapping(conn):
        try:
            return approved_mapping(conn, source)
        except NoApprovedMapping as error:
            raise Problem(409, "No approved mapping yet: propose and approve one first.") from error

    @router.get("", response_class=HTMLResponse)
    def demo_page():
        return page

    @router.get("/api/state")
    def state(request: Request):
        """The inventory rows as Shopify will see them, with each one's sync status."""
        pool = service(request).pool
        with pool.connection() as conn:
            mapping_id, mapping = current_mapping(conn)
            synced = {
                row[0]: {"synced_at": row[1], "quantity": row[2], "published": row[3]}
                for row in conn.execute("SELECT sku, synced_at, last_quantity, published FROM sync.items")
            }
            rows = read_rows(conn, source)

        products = []
        for row in rows:
            product = apply(mapping, row)
            sku = product.get("sku")
            products.append(
                {
                    "key": row.get(mapping.key_column),
                    "sku": sku,
                    "title": product.get("title"),
                    "price": product.get("price"),
                    "quantity": product.get("quantity"),
                    "status": product.get("status"),
                    "category": product.get("product_type"),
                    "image": product.get("image"),
                    "excluded": is_excluded(mapping, row),
                    "handle": handle_for(sku) if sku else None,
                    "shopify": synced.get(sku),
                }
            )
        products.sort(key=lambda product: product["sku"] or "")
        return {
            "table": source,
            "source": {"kind": "sheet" if on_sheet else "pos", "url": sheet_url},
            "mapping_id": mapping_id,
            "mapping": [field.model_dump() for field in mapping.fields],
            "products": products,
            "runs": service(request).runs(5),
            "storefront": {
                "url": settings.storefront_url or f"https://{settings.shopify_store}",
                "password": settings.storefront_password,
            },
        }

    def save_edits(edits: list[ProductEdit], request: Request) -> dict:
        """Writes price and stock edits to the inventory, like staff would. All or nothing.
        Returns each edited row's new price and stock cells, keyed by the row's key."""
        with service(request).pool.connection() as conn:
            _, mapping = current_mapping(conn)
            columns = {field.target: field.source_column for field in mapping.fields}
            saved = {}
            for edit in edits:
                changes = {}
                if edit.price is not None:
                    # A spreadsheet shows currency with its symbol, and Google returns it that way
                    changes[columns["price"]] = f"${edit.price:.2f}" if on_sheet else f"{edit.price:.2f}"
                if edit.quantity is not None and "quantity" in columns:
                    changes[columns["quantity"]] = str(edit.quantity)
                if not changes:
                    continue
                query = sql.SQL("UPDATE {} SET {} WHERE {} = %s RETURNING {}, {}").format(
                    sql.Identifier(*qualify(source)),
                    sql.SQL(", ").join(
                        sql.SQL("{} = %s").format(sql.Identifier(column)) for column in changes
                    ),
                    sql.Identifier(mapping.key_column),
                    sql.Identifier(columns["price"]),
                    sql.Identifier(columns["quantity"]) if "quantity" in columns else sql.NULL,
                )
                row = conn.execute(query, [*changes.values(), edit.key]).fetchone()
                if row is None:
                    raise HTTPException(status_code=404, detail=f"No product {edit.key}")
                saved[edit.key] = {"price": row[0], "quantity": row[1]}
        return saved

    def write_to_sheet(saved: dict, request: Request) -> dict | None:
        """Sends each edited row to Make, which finds it by SKU in the Google Sheet and updates it."""
        if not (on_sheet and saved and settings.make_sheet_webhook_url):
            return None
        written, failed = [], []
        for key, cells in saved.items():
            try:
                service(request).post(settings.make_sheet_webhook_url, {"sku": key, **cells})
                written.append(key)
            except Exception as error:
                log.warning("Writing %s to the sheet failed: %s", key, error)
                failed.append(key)
        return {"written": written, "failed": failed}

    @router.post("/api/sync")
    def sync_now(request: Request, body: SyncNow | None = None):
        """Saves the page's edits, syncs Shopify, then writes the edits into the sheet. One request."""
        slow_down(request, "sync", 6, 60)
        saved = save_edits(body.edits, request) if body and body.edits else {}
        report = service(request).sync(source, dry_run=False, trigger="demo")
        return {**report, "sheet": write_to_sheet(saved, request)}

    @router.post("/api/csv")
    def csv_preview(upload: CsvUpload, request: Request):
        """Claude maps an uploaded file. The answer is shown, never stored or synced."""
        slow_down(request, "csv", 3, 3600)
        try:
            uploaded = parse_csv(upload.content)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return service(request).preview_only(uploaded)

    return router
