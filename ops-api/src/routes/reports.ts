import { FastifyInstance } from "fastify";
import { z } from "zod";
import type { Deps } from "../app.js";
import { loadUsage } from "../services/guards.js";
import { localDate } from "../services/rules.js";

export async function reportRoutes(app: FastifyInstance, { pool, config }: Deps) {
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
    const usage = await loadUsage(pool, undefined);
    return {
      ok: true,
      last_7_days: result.rows,
      spent_today_usd: usage.spentTodayUsd,
      daily_budget_usd: config.dailyLlmBudgetUsd,
    };
  });

  app.get("/reports/events", async () => {
    const result = await pool.query(
      "SELECT id, kind, source, detail, created_at FROM events ORDER BY id DESC LIMIT 50"
    );
    return { ok: true, events: result.rows };
  });
}
