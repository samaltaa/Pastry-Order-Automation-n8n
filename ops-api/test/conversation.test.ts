import assert from "node:assert/strict";
import { test } from "node:test";
import { advance, emptyState, HISTORY_LIMIT, isExpired } from "../src/services/conversation.js";

test("history keeps only the most recent turns", () => {
  let state = emptyState();
  for (let i = 0; i < 10; i++) {
    state = advance(state, `message ${i}`, { valid: false, draft: null, clarification: "What time?" });
  }
  assert.equal(state.history.length, HISTORY_LIMIT);
  assert.equal(state.history.at(-1)?.text, "What time?");
});

test("conversations expire after the TTL", () => {
  const now = new Date("2026-10-27T12:00:00Z");
  assert.equal(isExpired(new Date("2026-10-27T11:00:00Z"), now), false);
  assert.equal(isExpired(new Date("2026-10-27T09:00:00Z"), now), true);
});
