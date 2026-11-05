import assert from "node:assert/strict";
import { after, before, describe, test } from "node:test";
import { Pool } from "pg";
import { buildApp } from "../src/app.js";
import { migrate } from "../src/db/migrate.js";

const url = process.env.TEST_DATABASE_URL;
const API_KEY = "test-key";

describe("API against a real database", { skip: !url && "TEST_DATABASE_URL not set" }, () => {
  const pool = new Pool({ connectionString: url });
  const app = buildApp(
    { pool, config: { databaseUrl: url!, apiKey: API_KEY, port: 0, chatRateLimitPerMinute: 6, dailyLlmBudgetUsd: 2 } },
    { logger: false }
  );
  const run = `test-${Date.now()}`;
  const headers = { "x-api-key": API_KEY };
  const order = {
    customer_name: "Test Customer",
    customer_phone: `tg:${run}`,
    delivery_address: "Calle El Conde 105",
    delivery_time: "2026-10-28T15:00",
    items: [{ item_id: 1, quantity: 2 }],
    idempotency_key: `${run}-order`,
  };

  before(async () => {
    await migrate(pool);
  });

  after(async () => {
    await pool.query("DELETE FROM orders WHERE idempotency_key LIKE $1", [`${run}%`]);
    await pool.query("DELETE FROM customers WHERE phone = $1", [`tg:${run}`]);
    await pool.query("DELETE FROM processed_updates WHERE update_id LIKE $1", [`${run}%`]);
    await app.close();
    await pool.end();
  });

  test("requests without the API key are rejected", async () => {
    const response = await app.inject({ method: "GET", url: "/menu" });
    assert.equal(response.statusCode, 401);
  });

  test("health is public", async () => {
    const response = await app.inject({ method: "GET", url: "/health" });
    assert.deepEqual(response.json(), { ok: true, db: true });
  });

  test("the same order sent twice creates one order", async () => {
    const first = await app.inject({ method: "POST", url: "/orders", headers, payload: order });
    const second = await app.inject({ method: "POST", url: "/orders", headers, payload: order });
    assert.equal(first.json().duplicate, false);
    assert.equal(second.json().duplicate, true);
    assert.equal(first.json().order.id, second.json().order.id);
    const count = await pool.query("SELECT COUNT(*)::int AS n FROM orders WHERE idempotency_key = $1", [order.idempotency_key]);
    assert.equal(count.rows[0].n, 1);
  });

  test("concurrent duplicates still create one order", async () => {
    const payload = { ...order, idempotency_key: `${run}-race` };
    const responses = await Promise.all(
      Array.from({ length: 5 }, () => app.inject({ method: "POST", url: "/orders", headers, payload }))
    );
    assert.equal(new Set(responses.map((r) => r.json().order.id)).size, 1);
  });

  test("the price comes from the database, not the request", async () => {
    const response = await app.inject({ method: "POST", url: "/orders", headers, payload: { ...order, idempotency_key: `${run}-price` } });
    assert.equal(response.json().order.total, 1300);
  });

  test("unknown menu items are rejected", async () => {
    const payload = { ...order, idempotency_key: `${run}-unknown`, items: [{ item_id: 99999, quantity: 1 }] };
    const response = await app.inject({ method: "POST", url: "/orders", headers, payload });
    assert.equal(response.statusCode, 400);
  });

  test("a Telegram update is only claimed once", async () => {
    const payload = { update_id: `${run}-1`, source: run };
    const first = await app.inject({ method: "POST", url: "/updates/claim", headers, payload });
    const second = await app.inject({ method: "POST", url: "/updates/claim", headers, payload });
    assert.equal(first.json().first, true);
    assert.equal(second.json().first, false);
  });
});
