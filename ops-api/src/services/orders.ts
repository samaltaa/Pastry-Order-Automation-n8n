import { Pool, PoolClient } from "pg";

export type OrderLine = { item_id: number; quantity: number };
export type PricedLine = OrderLine & { item_name: string; unit_price: number };
export type MenuPrices = Map<number, { price: number; item_name: string }>;

export type NewOrder = {
  customer_name: string;
  customer_phone: string;
  delivery_address: string;
  delivery_time: string;
  items: OrderLine[];
  notes?: string | null;
  idempotency_key: string;
  chat_id?: string;
};

export type CreateResult =
  | { status: "unknown_items"; missing: number[] }
  | { status: "created" | "duplicate"; orderId: number };

export function priceOrder(items: OrderLine[], prices: MenuPrices): { lines: PricedLine[]; total: number; missing: number[] } {
  const missing = items.filter((item) => !prices.has(item.item_id)).map((item) => item.item_id);
  const lines = items
    .filter((item) => prices.has(item.item_id))
    .map((item) => {
      const menuItem = prices.get(item.item_id)!;
      return { ...item, item_name: menuItem.item_name, unit_price: menuItem.price };
    });
  const cents = lines.reduce((sum, line) => sum + Math.round(line.unit_price * 100) * line.quantity, 0);
  return { lines, total: cents / 100, missing };
}

export async function menuPrices(db: Pool | PoolClient, ids: number[]): Promise<MenuPrices> {
  const result = await db.query(
    "SELECT id, price, item_name FROM menu_items WHERE id = ANY($1) AND is_active = true",
    [ids]
  );
  return new Map(result.rows.map((row) => [Number(row.id), { price: Number(row.price), item_name: row.item_name }]));
}

export async function orderWithItems(db: Pool | PoolClient, orderId: number) {
  const order = await db.query("SELECT * FROM orders WHERE id = $1", [orderId]);
  const items = await db.query(
    `
      SELECT oi.item_id, mi.item_name, oi.quantity, oi.unit_price
      FROM order_items oi
      JOIN menu_items mi ON mi.id = oi.item_id
      WHERE oi.order_id = $1
      ORDER BY oi.id
    `,
    [orderId]
  );
  return {
    order: { ...order.rows[0], total: Number(order.rows[0].total) },
    items: items.rows.map((row) => ({ ...row, unit_price: Number(row.unit_price) })),
  };
}

async function findByKey(db: Pool | PoolClient, key: string): Promise<number | null> {
  const result = await db.query("SELECT id FROM orders WHERE idempotency_key = $1", [key]);
  return result.rowCount ? result.rows[0].id : null;
}

export async function createOrder(pool: Pool, body: NewOrder): Promise<CreateResult> {
  const { lines, total, missing } = priceOrder(body.items, await menuPrices(pool, body.items.map((item) => item.item_id)));
  if (missing.length > 0) {
    return { status: "unknown_items", missing };
  }

  const existing = await findByKey(pool, body.idempotency_key);
  if (existing) {
    return { status: "duplicate", orderId: existing };
  }

  const db = await pool.connect();
  try {
    await db.query("BEGIN");

    const customer = await db.query(
      `
        INSERT INTO customers (name, phone)
        VALUES ($1, $2)
        ON CONFLICT (phone) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
      `,
      [body.customer_name, body.customer_phone]
    );
    const customerId = customer.rows[0].id;

    const order = await db.query(
      `
        INSERT INTO orders (customer_id, delivery_address, delivery_time, total, notes, idempotency_key)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING id
      `,
      [customerId, body.delivery_address, body.delivery_time, total, body.notes ?? null, body.idempotency_key]
    );

    if (!order.rowCount) {
      await db.query("ROLLBACK");
      return { status: "duplicate", orderId: (await findByKey(pool, body.idempotency_key))! };
    }

    const orderId = order.rows[0].id;
    await db.query("UPDATE customers SET last_address = $2 WHERE id = $1", [customerId, body.delivery_address]);
    if (body.chat_id) {
      await db.query("DELETE FROM conversations WHERE chat_id = $1", [body.chat_id]);
    }
    for (const line of lines) {
      await db.query(
        "INSERT INTO order_items (order_id, item_id, quantity, unit_price) VALUES ($1, $2, $3, $4)",
        [orderId, line.item_id, line.quantity, line.unit_price]
      );
    }

    await db.query("COMMIT");
    return { status: "created", orderId };
  } catch (error) {
    await db.query("ROLLBACK");
    throw error;
  } finally {
    db.release();
  }
}
