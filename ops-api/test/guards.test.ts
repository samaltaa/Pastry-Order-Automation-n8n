import assert from "node:assert/strict";
import { test } from "node:test";
import { guardViolation } from "../src/services/guards.js";

const LIMITS = { chatRateLimitPerMinute: 6, dailyLlmBudgetUsd: 2 };

test("normal usage passes", () => {
  assert.equal(guardViolation({ recentRequests: 2, spentTodayUsd: 0.5 }, LIMITS), null);
});

test("a chat over the per-minute limit is rate limited", () => {
  assert.equal(guardViolation({ recentRequests: 6, spentTodayUsd: 0 }, LIMITS)?.code, "rate_limited");
});

test("the daily budget stops all extraction", () => {
  assert.equal(guardViolation({ recentRequests: 0, spentTodayUsd: 2 }, LIMITS)?.code, "budget_exceeded");
});
