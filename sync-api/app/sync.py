"""Pushes POS rows to Shopify with the approved mapping. The POS is the source of truth.

For each row:
- New SKU: create the product with its stock (productSet).
- Product data changed (title, price, ...): update the product (productSet).
- Stock changed: set it (inventorySetQuantities). changeFromQuantity is the quantity this sync last set,
  so if Shopify's stock moved since then (an online sale), Shopify refuses. That's reported as a
  conflict, and the POS quantity is applied.
- Nothing changed: no Shopify call at all. sync.items remembers a hash of what was last sent.
"""

import hashlib
import json
import re

from psycopg import Connection
from psycopg.rows import dict_row

from app.mapping import REQUIRED_TARGETS, Mapping, apply, is_excluded
from app.pos import POS_SCHEMA, read_rows
from app.shopify import ShopifyClient, ShopifyError

LOCK_ID = 7_270_002
SHOPIFY_IDS = ("shopify_product_id", "shopify_variant_id", "shopify_inventory_item_id")

PRODUCT_SET = """
mutation ProductSet($identifier: ProductSetIdentifiers, $input: ProductSetInput!) {
  productSet(identifier: $identifier, input: $input, synchronous: true) {
    product { id variants(first: 1) { nodes { id inventoryItem { id } } } }
    userErrors { field message code }
  }
}
"""

SET_STOCK = """
mutation SetStock($input: InventorySetQuantitiesInput!, $idempotencyKey: String!) {
  inventorySetQuantities(input: $input) @idempotent(key: $idempotencyKey) {
    userErrors { field message code }
  }
}
"""

READ_STOCK = """
query ReadStock($id: ID!, $locationId: ID!) {
  inventoryItem(id: $id) {
    inventoryLevel(locationId: $locationId) { quantities(names: ["available"]) { name quantity } }
  }
}
"""

FIRST_LOCATION = "{ locations(first: 1) { nodes { id } } }"


class NoApprovedMapping(Exception):
    pass


class SyncAlreadyRunning(Exception):
    pass


def product_hash(product: dict) -> str:
    """Fingerprint of everything except stock, which is compared separately."""
    data = {key: value for key, value in product.items() if key != "quantity"}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def handle_for(sku: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", sku.lower()).strip("-")


def product_input(product: dict, item: dict | None, location_id: str | None) -> tuple[dict, dict]:
    """The productSet arguments. A known product is matched by its Shopify ID, a new one by handle."""
    variant = {
        "optionValues": [{"optionName": "Title", "name": "Default Title"}],
        "sku": product["sku"],
        "price": product["price"],
        "barcodes": [{"value": product["barcode"]}] if product.get("barcode") else [],
        "inventoryItem": {"tracked": True},
    }
    if product.get("taxable") is not None:
        variant["taxable"] = product["taxable"]
    if product.get("cost") is not None:
        variant["inventoryItem"]["cost"] = product["cost"]

    data = {
        "title": product["title"],
        "productOptions": [{"name": "Title", "values": [{"name": "Default Title"}]}],
        "variants": [variant],
    }
    if product.get("product_type"):
        data["productType"] = product["product_type"]
    if product.get("status"):
        data["status"] = product["status"]

    if item:
        variant["id"] = item["shopify_variant_id"]
        return {"id": item["shopify_product_id"]}, data

    handle = handle_for(product["sku"])
    data["handle"] = handle
    if product.get("quantity") is not None and location_id:
        variant["inventoryQuantities"] = [
            {"locationId": location_id, "name": "available", "quantity": product["quantity"]}
        ]
    return {"handle": handle}, data


class Sync:
    def __init__(self, conn: Connection, shopify: ShopifyClient, location_id: str | None = None):
        self.conn = conn
        self.shopify = shopify
        self.location_id = location_id

    def location(self) -> str:
        if not self.location_id:
            self.location_id = self.shopify.graphql(FIRST_LOCATION)["locations"]["nodes"][0]["id"]
        return self.location_id

    def upsert_product(self, product: dict, item: dict | None) -> dict:
        identifier, data = product_input(product, item, None if item else self.location())
        result = self.shopify.graphql(PRODUCT_SET, {"identifier": identifier, "input": data})["productSet"]
        if result["userErrors"]:
            raise ShopifyError(result["userErrors"])
        variant = result["product"]["variants"]["nodes"][0]
        return {
            "shopify_product_id": result["product"]["id"],
            "shopify_variant_id": variant["id"],
            "shopify_inventory_item_id": variant["inventoryItem"]["id"],
        }

    def set_stock(self, item: dict, quantity: int, key: str) -> int | None:
        """Sets stock. Returns Shopify's quantity if it had changed since the last sync (a conflict)."""
        expected = item["last_quantity"]
        errors = self._set_stock(item, quantity, expected, f"{key}-1")
        if not errors:
            return None
        if any(error.get("code") == "CHANGE_FROM_QUANTITY_STALE" for error in errors):
            level = self.shopify.graphql(
                READ_STOCK, {"id": item["shopify_inventory_item_id"], "locationId": self.location()}
            )["inventoryItem"]["inventoryLevel"]
            current = level["quantities"][0]["quantity"]
            errors = self._set_stock(item, quantity, current, f"{key}-2")
            if not errors:
                return current
        raise ShopifyError(errors)

    def _set_stock(self, item: dict, quantity: int, change_from: int | None, key: str) -> list[dict]:
        result = self.shopify.graphql(
            SET_STOCK,
            {
                "idempotencyKey": key,
                "input": {
                    "name": "available",
                    "reason": "correction",
                    "quantities": [
                        {
                            "inventoryItemId": item["shopify_inventory_item_id"],
                            "locationId": self.location(),
                            "quantity": quantity,
                            "changeFromQuantity": change_from,
                        }
                    ],
                },
            },
        )
        return result["inventorySetQuantities"]["userErrors"]


def approved_mapping(conn: Connection, source_table: str) -> tuple[int, Mapping]:
    row = conn.execute(
        """
        SELECT id, mapping FROM sync.mappings
        WHERE source_table = %s AND status = 'approved'
        ORDER BY reviewed_at DESC LIMIT 1
        """,
        (source_table,),
    ).fetchone()
    if not row:
        raise NoApprovedMapping(f"No approved mapping for {source_table}")
    return row[0], Mapping.model_validate(row[1])


def save_item(conn: Connection, sku: str, source_key: str, data_hash: str, ids: dict, quantity) -> None:
    conn.execute(
        """
        INSERT INTO sync.items (sku, source_key, data_hash, shopify_product_id, shopify_variant_id,
                                shopify_inventory_item_id, last_quantity, last_error, synced_at)
        VALUES (%(sku)s, %(source_key)s, %(data_hash)s, %(shopify_product_id)s, %(shopify_variant_id)s,
                %(shopify_inventory_item_id)s, %(quantity)s, NULL, NOW())
        ON CONFLICT (sku) DO UPDATE SET
          source_key = EXCLUDED.source_key, data_hash = EXCLUDED.data_hash,
          shopify_product_id = EXCLUDED.shopify_product_id, shopify_variant_id = EXCLUDED.shopify_variant_id,
          shopify_inventory_item_id = EXCLUDED.shopify_inventory_item_id,
          last_quantity = EXCLUDED.last_quantity, last_error = NULL, synced_at = NOW()
        """,
        {"sku": sku, "source_key": source_key, "data_hash": data_hash, "quantity": quantity, **ids},
    )


def run_sync(
    conn: Connection, shopify: ShopifyClient, table: str, dry_run: bool, trigger: str, location_id=None
) -> dict:
    """One sync run. The connection is held for the whole run so the advisory lock stops overlaps."""
    if not conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0]:
        raise SyncAlreadyRunning("A sync is already running")
    try:
        source_table = f"{POS_SCHEMA}.{table}"
        mapping_id, mapping = approved_mapping(conn, source_table)
        run_id = conn.execute(
            "INSERT INTO sync.runs (mapping_id, trigger, dry_run) VALUES (%s, %s, %s) RETURNING id",
            (mapping_id, trigger, dry_run),
        ).fetchone()[0]
        conn.commit()
        try:
            report = sync_rows(conn, Sync(conn, shopify, location_id), mapping, table, dry_run, run_id)
        except Exception as error:
            conn.rollback()
            conn.execute(
                "UPDATE sync.runs SET status = 'failed', error = %s, finished_at = NOW() WHERE id = %s",
                (str(error)[:1000], run_id),
            )
            conn.commit()
            raise
        conn.execute(
            """
            UPDATE sync.runs SET status = 'succeeded', finished_at = NOW(), created = %(created)s,
              updated = %(updated)s, stock_updated = %(stock_updated)s, skipped = %(unchanged)s,
              failed = %(failed)s, conflicts = %(conflicts)s
            WHERE id = %(run_id)s
            """,
            {**report["counts"], "run_id": run_id},
        )
        conn.commit()
        return {"run_id": run_id, "mapping_id": mapping_id, "dry_run": dry_run, **report}
    finally:
        conn.rollback()
        conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
        conn.commit()


def sync_rows(conn: Connection, sync: Sync, mapping: Mapping, table: str, dry_run: bool, run_id: int) -> dict:
    counts = dict.fromkeys(
        ["created", "updated", "stock_updated", "unchanged", "excluded", "failed", "conflicts"], 0
    )
    changes: list[dict] = []
    known = {row["sku"]: row for row in conn.cursor(row_factory=dict_row).execute("SELECT * FROM sync.items")}

    for row in read_rows(conn, table):
        if is_excluded(mapping, row):
            counts["excluded"] += 1
            continue
        product = apply(mapping, row)
        missing = sorted(target for target in REQUIRED_TARGETS if not product.get(target))
        source_key = str(row.get(mapping.key_column, "")).strip()
        if missing:
            counts["failed"] += 1
            changes.append({"sku": source_key, "action": "failed", "detail": f"Missing {', '.join(missing)}"})
            continue

        sku, quantity, data_hash = product["sku"], product.get("quantity"), product_hash(product)
        item = known.get(sku)
        data_changed = item is None or item["data_hash"] != data_hash
        stock_changed = item is not None and quantity is not None and quantity != item["last_quantity"]
        if not data_changed and not stock_changed:
            counts["unchanged"] += 1
            continue

        action = "create" if item is None else "update" if data_changed else "stock"
        change = {"sku": sku, "action": action}
        if stock_changed:
            change["detail"] = f"stock {item['last_quantity']} -> {quantity}"
        if dry_run:
            if data_changed:
                counts["created" if item is None else "updated"] += 1
            if stock_changed:
                counts["stock_updated"] += 1
            changes.append(change)
            continue

        try:
            ids = {key: item[key] for key in SHOPIFY_IDS} if item else {}
            if data_changed:
                ids = sync.upsert_product(product, item)
                counts["created" if item is None else "updated"] += 1
            if stock_changed:
                shopify_quantity = sync.set_stock({**item, **ids}, quantity, f"run{run_id}-{sku}")
                counts["stock_updated"] += 1
                if shopify_quantity is not None:
                    counts["conflicts"] += 1
                    change["detail"] = (
                        f"Shopify had {shopify_quantity}, expected {item['last_quantity']}; "
                        f"POS value {quantity} applied"
                    )
                    change["conflict"] = True
            save_item(conn, sku, source_key, data_hash, ids, quantity)
            conn.commit()
            changes.append(change)
        except ShopifyError as error:
            conn.rollback()
            counts["failed"] += 1
            conn.execute("UPDATE sync.items SET last_error = %s WHERE sku = %s", (str(error)[:500], sku))
            conn.commit()
            changes.append({"sku": sku, "action": "failed", "detail": str(error)[:300]})

    return {"counts": counts, "changes": changes}
