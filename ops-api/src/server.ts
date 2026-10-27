import "dotenv/config";
import Fastify from "fastify";
import { Pool } from "pg";
import { z } from "zod";
import { extractOrder } from "./llm/extract.js";
import { localDate, MenuItem } from "./rules.js";

const app = Fastify({ logger: true });

const databaseUrl =
  process.env.DATABASE_URL ||
  "postgresql://pastry:change_me@localhost:5432/pastry_ops";

const apiKey = process.env.OPS_API_KEY;
if (!apiKey) {
  throw new Error("OPS_API_KEY is required");
}

const pool = new Pool({ connectionString: databaseUrl });

const itemSchema = z.object({
  item_id: z.number().int().positive(),
  quantity: z.number().int().positive().max(100),
});

const orderSchema = z.object({
  customer_name: z.string().min(2),
  customer_phone: z.string().min(3),
  delivery_address: z.string().min(4),
  delivery_time: z.string().min(3),
  items: z.array(itemSchema).min(1),
  notes: z.string().nullish(),
  idempotency_key: z.string().min(1),
});

const extractSchema = z.object({
  message: z.string().min(1).max(2000),
  ref: z.string().optional(),
});

const claimSchema = z.object({
  update_id: z.union([z.string(), z.number()]).transform(String),
  source: z.string().min(1),
});

async function loadMenu(): Promise<MenuItem[]> {
  const result = await pool.query(
    "SELECT id, item_name, category, price FROM menu_items WHERE is_active = true ORDER BY id"
  );
  return result.rows.map((row) => ({
    id: row.id,
    item_name: row.item_name,
    category: row.category,
    price: Number(row.price),
  }));
}

async function orderWithItems(orderId: number) {
  const order = await pool.query("SELECT * FROM orders WHERE id = $1", [orderId]);
  const items = await pool.query(
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

app.addHook("onRequest", async (request, reply) => {
  if (request.url === "/health") return;
  if (request.headers["x-api-key"] !== apiKey) {
    reply.code(401).send({ ok: false, message: "Invalid API key." });
  }
});

app.setErrorHandler((error, request, reply) => {
  if (error instanceof z.ZodError) {
    reply.code(400).send({ ok: false, message: "Invalid request.", issues: error.issues });
    return;
  }
  request.log.error(error);
  reply.code(500).send({ ok: false, message: "Internal error." });
});

app.get("/health", async () => {
  const result = await pool.query("SELECT 1 AS ok");
  return {
    ok: true,
    db: result.rows[0]?.ok === 1,
  };
});

app.get("/menu", async () => loadMenu());

app.post("/updates/claim", async (request) => {
  const { update_id, source } = claimSchema.parse(request.body);
  const result = await pool.query(
    "INSERT INTO processed_updates (update_id, source) VALUES ($1, $2) ON CONFLICT DO NOTHING",
    [`${source}:${update_id}`, source]
  );
  return { ok: true, first: result.rowCount === 1 };
});

app.post("/llm/extract-order", async (request, reply) => {
  const { message, ref } = extractSchema.parse(request.body);
  const menu = await loadMenu();
  const extraction = await extractOrder(message, menu);

  for (const call of extraction.calls) {
    await pool.query(
      `
        INSERT INTO llm_calls (route, ref, model, input_tokens, output_tokens, cost_usd, latency_ms, outcome, error)
        VALUES ('extract-order', $1, $2, $3, $4, $5, $6, $7, $8)
      `,
      [ref ?? null, call.model, call.inputTokens, call.outputTokens, call.costUsd, call.latencyMs, call.outcome, call.error ?? null]
    );
  }

  if (!extraction.draft) {
    reply.code(502);
    return { ok: false, message: "Extraction failed." };
  }

  return {
    ok: true,
    valid: extraction.valid,
    items: extraction.draft.items,
    delivery_address: extraction.draft.delivery_address,
    delivery_at: extraction.draft.delivery_at,
    customer_name: extraction.draft.customer_name,
    notes: extraction.draft.notes,
    clarification: extraction.clarification,
    violations: extraction.violations.map((v) => v.code),
    model: extraction.model,
    escalated: extraction.escalated,
  };
});

app.post("/orders", async (request, reply) => {
  const body = orderSchema.parse(request.body);

  const menuResult = await pool.query(
    "SELECT id, price, item_name FROM menu_items WHERE id = ANY($1) AND is_active = true",
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

  const existing = await pool.query("SELECT id FROM orders WHERE idempotency_key = $1", [body.idempotency_key]);
  if (existing.rowCount) {
    return { ok: true, duplicate: true, ...(await orderWithItems(existing.rows[0].id)) };
  }

  const total = body.items.reduce((sum, row) => {
    const item = menuMap.get(row.item_id);
    return sum + Number(item?.price ?? 0) * row.quantity;
  }, 0);

  const db = await pool.connect();
  try {
    await db.query("BEGIN");

    const customerResult = await db.query(
      `
        INSERT INTO customers (name, phone)
        VALUES ($1, $2)
        ON CONFLICT (phone) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
      `,
      [body.customer_name, body.customer_phone]
    );

    const orderResult = await db.query(
      `
        INSERT INTO orders (customer_id, delivery_address, delivery_time, total, notes, idempotency_key)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING id
      `,
      [customerResult.rows[0].id, body.delivery_address, body.delivery_time, total, body.notes ?? null, body.idempotency_key]
    );

    if (!orderResult.rowCount) {
      await db.query("ROLLBACK");
      const raced = await pool.query("SELECT id FROM orders WHERE idempotency_key = $1", [body.idempotency_key]);
      return { ok: true, duplicate: true, ...(await orderWithItems(raced.rows[0].id)) };
    }

    const orderId = orderResult.rows[0].id;
    for (const item of body.items) {
      await db.query(
        `
          INSERT INTO order_items (order_id, item_id, quantity, unit_price)
          VALUES ($1, $2, $3, $4)
        `,
        [orderId, item.item_id, item.quantity, menuMap.get(item.item_id)?.price ?? 0]
      );
    }

    await db.query("COMMIT");
    return { ok: true, duplicate: false, ...(await orderWithItems(orderId)) };
  } catch (error) {
    await db.query("ROLLBACK");
    throw error;
  } finally {
    db.release();
  }
});

app.get("/orders/:id", async (request, reply) => {
  const { id } = request.params as { id: string };

  const orderResult = await pool.query("SELECT id FROM orders WHERE id = $1", [id]);

  if (orderResult.rowCount === 0) {
    reply.code(404);
    return { ok: false, message: "Order not found." };
  }

  return { ok: true, ...(await orderWithItems(orderResult.rows[0].id)) };
});

app.post("/orders/:id/status", async (request, reply) => {
  const { id } = request.params as { id: string };
  const { status } = z.object({ status: z.enum(["pending", "baking", "out_for_delivery", "delivered", "cancelled"]) }).parse(request.body);

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

app.get("/reports/prep-list", async (request) => {
  const { date } = z.object({ date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional() }).parse(request.query);
  const day = date ?? localDate(new Date());

  const totals = await pool.query(
    `
      SELECT mi.item_name, SUM(oi.quantity)::int AS quantity
      FROM orders o
      JOIN order_items oi ON oi.order_id = o.id
      JOIN menu_items mi ON mi.id = oi.item_id
      WHERE o.delivery_time LIKE $1 AND o.status <> 'cancelled'
      GROUP BY mi.item_name
      ORDER BY mi.item_name
    `,
    [`${day}%`]
  );

  const orders = await pool.query(
    `
      SELECT o.id, c.name AS customer, o.delivery_time, o.delivery_address, o.total, o.notes
      FROM orders o
      LEFT JOIN customers c ON c.id = o.customer_id
      WHERE o.delivery_time LIKE $1 AND o.status <> 'cancelled'
      ORDER BY o.delivery_time
    `,
    [`${day}%`]
  );

  const lines = [
    `Prep list for ${day}`,
    "",
    ...(totals.rows.length ? totals.rows.map((r) => `• ${r.quantity}× ${r.item_name}`) : ["No orders today."]),
    ...(orders.rows.length ? ["", "Deliveries:"] : []),
    ...orders.rows.map(
      (o) => `#${o.id} ${o.delivery_time.slice(11)} ${o.customer ?? "Guest"}, ${o.delivery_address}${o.notes ? ` (${o.notes})` : ""}`
    ),
  ];

  return {
    ok: true,
    date: day,
    totals: totals.rows,
    orders: orders.rows.map((o) => ({ ...o, total: Number(o.total) })),
    text: lines.join("\n"),
  };
});

app.get("/reports/llm-usage", async () => {
  const result = await pool.query(
    `
      SELECT model, outcome, COUNT(*)::int AS calls, SUM(cost_usd)::float AS cost_usd, AVG(latency_ms)::int AS avg_latency_ms
      FROM llm_calls
      WHERE created_at > NOW() - INTERVAL '7 days'
      GROUP BY model, outcome
      ORDER BY model, outcome
    `
  );
  return { ok: true, last_7_days: result.rows };
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
