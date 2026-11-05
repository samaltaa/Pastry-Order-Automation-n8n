import { FastifyInstance } from "fastify";
import { z } from "zod";
import type { Deps } from "../app.js";
import { extractOrder } from "../llm/extract.js";
import { advance, ConversationContext, emptyState, isExpired } from "../services/conversation.js";
import { guardViolation, loadUsage, recordEventOncePerDay } from "../services/guards.js";
import { loadMenu } from "../services/menu.js";

const extractSchema = z.object({
  message: z.string().min(1).max(2000),
  ref: z.string().optional(),
  chat_id: z.string().optional(),
});

export async function llmRoutes(app: FastifyInstance, { pool, config }: Deps) {
  app.post("/llm/extract-order", async (request, reply) => {
    const { message, ref, chat_id } = extractSchema.parse(request.body);

    const blocked = guardViolation(await loadUsage(pool, chat_id), config);
    if (blocked) {
      request.log.warn({ chat_id, code: blocked.code }, "extraction blocked by guard");
      if (blocked.code === "budget_exceeded") {
        await recordEventOncePerDay(pool, "budget_exceeded", { budget_usd: config.dailyLlmBudgetUsd });
      }
      return {
        ok: true,
        valid: false,
        items: [],
        delivery_address: null,
        delivery_at: null,
        customer_name: null,
        notes: null,
        clarification: blocked.message,
        violations: [blocked.code],
        model: null,
        escalated: false,
        turns_remembered: 0,
      };
    }

    const menu = await loadMenu(pool);
    const now = new Date();
    const db = await pool.connect();
    try {
      await db.query("BEGIN");

      let context: ConversationContext | null = null;
      if (chat_id) {
        await db.query(
          "INSERT INTO conversations (chat_id, state) VALUES ($1, $2) ON CONFLICT (chat_id) DO NOTHING",
          [chat_id, emptyState()]
        );
        const conversation = await db.query(
          "SELECT state, updated_at FROM conversations WHERE chat_id = $1 FOR UPDATE",
          [chat_id]
        );
        const customer = await db.query("SELECT last_address FROM customers WHERE phone = $1", [`tg:${chat_id}`]);
        const row = conversation.rows[0];
        const state = isExpired(new Date(row.updated_at), now) ? emptyState() : { ...emptyState(), ...row.state };
        context = { ...state, savedAddress: customer.rows[0]?.last_address ?? null };
      }

      const extraction = await extractOrder(message, menu, now, undefined, context);

      for (const call of extraction.calls) {
        await db.query(
          `
            INSERT INTO llm_calls (route, ref, chat_id, model, input_tokens, output_tokens, cost_usd, latency_ms, outcome, error)
            VALUES ('extract-order', $1, $2, $3, $4, $5, $6, $7, $8, $9)
          `,
          [ref ?? null, chat_id ?? null, call.model, call.inputTokens, call.outputTokens, call.costUsd, call.latencyMs, call.outcome, call.error ?? null]
        );
      }

      if (chat_id && context && extraction.draft) {
        const next = advance(context, message, extraction);
        await db.query(
          "UPDATE conversations SET state = $2, last_message = $3, updated_at = NOW() WHERE chat_id = $1",
          [chat_id, next, message]
        );
      }

      await db.query("COMMIT");

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
        turns_remembered: context?.history.length ?? 0,
      };
    } catch (error) {
      await db.query("ROLLBACK");
      throw error;
    } finally {
      db.release();
    }
  });
}
