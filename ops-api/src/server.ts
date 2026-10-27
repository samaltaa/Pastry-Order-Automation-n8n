import "dotenv/config";
import Fastify from "fastify";
import { Pool } from "pg";
import { z } from "zod";

const app = Fastify({ logger: true });

const databaseUrl =
  process.env.DATABASE_URL ||
  "postgresql://pastry:change_me@localhost:5432/pastry_ops";

const pool = new Pool({ connectionString: databaseUrl });

const itemSchema = z.object({
  item_id: z.number().int().positive(),
  quantity: z.number().int().positive(),
});

const orderSchema = z.object({
  customer_name: z.string().min(2),
  customer_phone: z.string().min(6),
  delivery_address: z.string().min(4),
  delivery_time: z.string().min(3),
  items: z.array(itemSchema).min(1),
  notes: z.string().optional(),
  idempotency_key: z.string().optional(),
});

app.get("/health", async () => {
  const result = await pool.query("SELECT 1 AS ok");
  return {
    ok: true,
    db: result.rows[0]?.ok === 1,
  };
});

app.get("/menu", async () => {
  const result = await pool.query(
    "SELECT id, item_name, category, price FROM menu_items WHERE is_active = true ORDER BY id"
  );

  return result.rows.map((row) => ({
    id: row.id,
    item_name: row.item_name,
    category: row.category,
    price: Number(row.price),
  }));
});

app.post("/llm/extract-order", async (request) => {
  const body = request.body as any;

  const message = String(body?.message ?? "");
  const menuItems = Array.isArray(body?.menu_items) ? body.menu_items : [];

  if (!message) {
    return {
      ok: false,
      valid: false,
      message: "No message supplied.",
    };
  }

  const fallback = {
    ok: true,
    valid: true,
    confidence: 0.9,
    items: menuItems.length
      ? menuItems.slice(0, 2).map((item: { id: number }) => ({ item_id: item.id, quantity: 1 }))
      : [{ item_id: 1, quantity: 2 }],
    delivery_address: "Naco",
    delivery_time: "tomorrow at 3pm",
    customer_name: "Guest",
    customer_phone: "00000000",
    notes: "Fallback extraction for local demo mode",
  };

  return fallback;
});

app.post("/orders", async (request, reply) => {
  const body = orderSchema.parse(request.body);

  const menuResult = await pool.query(
    "SELECT id, price, item_name FROM menu_items WHERE id = ANY($1)",
    [body.items.map((item) => item.item_id)]
  );

  const menuMap = new Map(
    menuResult.rows.map((row) => [Number(row.id), { price: Number(row.price), item_name: row.item_name }])
  );

  const missingIds = body.items
    .filter((item) => !menuMap.has(item.item_id))
    .map((item) => item.item_id);

  if (missingIds.length > 0) {
    reply.code(400);
    return {
      ok: false,
      message: "Unknown menu item(s): " + missingIds.join(", "),
    };
  }

  const total = body.items.reduce((sum, row) => {
    const item = menuMap.get(row.item_id);
    return sum + Number(item?.price ?? 0) * row.quantity;
  }, 0);

  const customerResult = await pool.query(
    `
      INSERT INTO customers (name, phone)
      VALUES ($1, $2)
      ON CONFLICT (phone) DO UPDATE SET name = EXCLUDED.name
      RETURNING id
    `,
    [body.customer_name, body.customer_phone]
  );

  const customerId = customerResult.rows[0].id;

  const orderResult = await pool.query(
    `
      INSERT INTO orders (customer_id, delivery_address, delivery_time, total, notes, idempotency_key)
      VALUES ($1, $2, $3, $4, $5, $6)
      ON CONFLICT (idempotency_key) DO NOTHING
      RETURNING *
    `,
    [
      customerId,
      body.delivery_address,
      body.delivery_time,
      total,
      body.notes ?? null,
      body.idempotency_key ?? `order-${Date.now()}`,
    ]
  );

  const insertedOrder = orderResult.rows[0];

  if (!insertedOrder) {
    const existingOrder = await pool.query(
      `SELECT * FROM orders WHERE idempotency_key = $1`,
      [body.idempotency_key ?? `order-${Date.now()}`]
    );

    const rate = existingOrder.rows[0];
    return {
      ok: true,
      duplicate: true,
      order: rate,
    };
  }

  for (const item of body.items) {
    await pool.query(
      `
        INSERT INTO order_items (order_id, item_id, quantity, unit_price)
        VALUES ($1, $2, $3, $4)
      `,
      [insertedOrder.id, item.item_id, item.quantity, menuMap.get(item.item_id)?.price ?? 0]
    );
  }

  return {
    ok: true,
    order: {
      ...insertedOrder,
      total: Number(insertedOrder.total),
      item_count: body.items.length,
    },
  };
});

app.get("/orders/:id", async (request, reply) => {
  const { id } = request.params as { id: string };

  const orderResult = await pool.query("SELECT * FROM orders WHERE id = $1", [id]);

  if (orderResult.rowCount === 0) {
    reply.code(404);
    return { ok: false, message: "Order not found." };
  }

  const itemsResult = await pool.query(
    `
      SELECT oi.*, mi.item_name
      FROM order_items oi
      JOIN menu_items mi ON mi.id = oi.item_id
      WHERE oi.order_id = $1
    `,
    [id]
  );

  return {
    ok: true,
    order: orderResult.rows[0],
    items: itemsResult.rows,
  };
});

app.post("/orders/:id/status", async (request, reply) => {
  const { id } = request.params as { id: string };
  const { status } = z.object({ status: z.string().min(2) }).parse(request.body);

  const result = await pool.query(
    `UPDATE orders SET status = $1, updated_at = NOW() WHERE id = $2 RETURNING *`,
    [status, id]
  );

  if (result.rowCount === 0) {
    reply.code(404);
    return { ok: false, message: "Order not found." };
  }

  return {
    ok: true,
    order: result.rows[0],
  };
});

const port = Number(process.env.PORT ?? 3001);

app.listen({ port, host: "0.0.0.0" })
  .then(() => {
    console.log(`Pastry Ops API listening on http://0.0.0.0:${port}`);
  })
  .catch((error) => {
    console.error("Failed to start API", error);
    process.exit(1);
  });
