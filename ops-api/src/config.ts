import "dotenv/config";

export type Config = {
  databaseUrl: string;
  apiKey: string;
  port: number;
  chatRateLimitPerMinute: number;
  dailyLlmBudgetUsd: number;
};

export function loadConfig(): Config {
  const apiKey = process.env.OPS_API_KEY;
  if (!apiKey) {
    throw new Error("OPS_API_KEY is required");
  }
  return {
    databaseUrl: process.env.DATABASE_URL || "postgresql://pastry:change_me@localhost:5432/pastry_ops",
    apiKey,
    port: Number(process.env.PORT ?? 3001),
    chatRateLimitPerMinute: Number(process.env.CHAT_RATE_LIMIT_PER_MINUTE ?? 6),
    dailyLlmBudgetUsd: Number(process.env.DAILY_LLM_BUDGET_USD ?? 2),
  };
}
