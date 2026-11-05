import { Pool, PoolClient } from "pg";
import { MenuItem } from "./rules.js";

export async function loadMenu(db: Pool | PoolClient): Promise<MenuItem[]> {
  const result = await db.query(
    "SELECT id, item_name, category, price FROM menu_items WHERE is_active = true ORDER BY id"
  );
  return result.rows.map((row) => ({
    id: row.id,
    item_name: row.item_name,
    category: row.category,
    price: Number(row.price),
  }));
}
