import { FastifyInstance } from "fastify";
import { z } from "zod";
import type { Deps } from "../app.js";
import { loadMenu } from "../services/menu.js";

const claimSchema = z.object({
  update_id: z.union([z.string(), z.number()]).transform(String),
  source: z.string().min(1),
});

const eventSchema = z.object({
  kind: z.string().min(1).max(60),
  source: z.string().max(120).optional(),
  detail: z.record(z.string(), z.unknown()).default({}),
});

export async function systemRoutes(app: FastifyInstance, { pool }: Deps) {
  app.get("/health", async () => {
    const result = await pool.query("SELECT 1 AS ok");
    return { ok: true, db: result.rows[0]?.ok === 1 };
  });

  app.get("/menu", async () => loadMenu(pool));

  app.post("/updates/claim", async (request) => {
    const { update_id, source } = claimSchema.parse(request.body);
    const result = await pool.query(
      "INSERT INTO processed_updates (update_id, source) VALUES ($1, $2) ON CONFLICT DO NOTHING",
      [`${source}:${update_id}`, source]
    );
    return { ok: true, first: result.rowCount === 1 };
  });

  app.post("/events", async (request) => {
    const { kind, source, detail } = eventSchema.parse(request.body);
    const result = await pool.query(
      "INSERT INTO events (kind, source, detail) VALUES ($1, $2, $3) RETURNING id",
      [kind, source ?? null, detail]
    );
    return { ok: true, id: result.rows[0].id };
  });

  app.post("/conversations/reset", async (request) => {
    const { chat_id } = z.object({ chat_id: z.string().min(1) }).parse(request.body);
    const result = await pool.query("DELETE FROM conversations WHERE chat_id = $1", [chat_id]);
    return { ok: true, cleared: (result.rowCount ?? 0) > 0 };
  });
}
