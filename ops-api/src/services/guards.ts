import { Pool } from "pg";
import { TIMEZONE } from "./rules.js";

export type Usage = { recentRequests: number; spentTodayUsd: number };
export type Limits = { chatRateLimitPerMinute: number; dailyLlmBudgetUsd: number };
export type GuardViolation = { code: "budget_exceeded" | "rate_limited"; message: string };

export function guardViolation(usage: Usage, limits: Limits): GuardViolation | null {
  if (usage.spentTodayUsd >= limits.dailyLlmBudgetUsd) {
    return {
      code: "budget_exceeded",
      message: "Our order assistant is resting for today. Please message us again tomorrow, or send /menu to see what we bake.",
    };
  }
  if (usage.recentRequests >= limits.chatRateLimitPerMinute) {
    return {
      code: "rate_limited",
      message: "That's a lot of messages at once! Please wait a minute and send your order again.",
    };
  }
  return null;
}

export async function loadUsage(pool: Pool, chatId: string | undefined): Promise<Usage> {
  const spent = await pool.query(
    `
      SELECT COALESCE(SUM(cost_usd), 0)::float AS spent
      FROM llm_calls
      WHERE created_at >= (date_trunc('day', NOW() AT TIME ZONE $1) AT TIME ZONE $1)
    `,
    [TIMEZONE]
  );
  const recent = chatId
    ? await pool.query(
        `
          SELECT COUNT(DISTINCT COALESCE(ref, id::text))::int AS requests
          FROM llm_calls
          WHERE chat_id = $1 AND created_at > NOW() - INTERVAL '1 minute'
        `,
        [chatId]
      )
    : null;
  return { spentTodayUsd: spent.rows[0].spent, recentRequests: recent?.rows[0].requests ?? 0 };
}

export async function recordEventOncePerDay(pool: Pool, kind: string, detail: object): Promise<void> {
  await pool.query(
    `
      INSERT INTO events (kind, source, detail)
      SELECT $1, 'ops-api', $2
      WHERE NOT EXISTS (
        SELECT 1 FROM events WHERE kind = $1 AND created_at >= (date_trunc('day', NOW() AT TIME ZONE $3) AT TIME ZONE $3)
      )
    `,
    [kind, detail, TIMEZONE]
  );
}
