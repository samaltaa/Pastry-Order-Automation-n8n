import assert from "node:assert/strict";
import { test } from "node:test";
import { checkDeliveryTime, checkDraft, clarificationFor, MenuItem, OrderDraft } from "../src/services/rules.js";

const NOW = new Date("2026-10-27T10:00:00-04:00");
const MENU: MenuItem[] = [{ id: 1, item_name: "Tres Leches", category: "cake", price: 650 }];

function draft(overrides: Partial<OrderDraft> = {}): OrderDraft {
  return {
    items: [{ item_id: 1, quantity: 2 }],
    unmatched_items: [],
    needs_clarification: false,
    delivery_address: "Calle El Conde 105",
    delivery_at: "2026-10-28T15:00",
    customer_name: null,
    notes: null,
    confidence: 0.9,
    clarification: null,
    ...overrides,
  };
}

const codes = (d: OrderDraft) => checkDraft(d, MENU, NOW).map((v) => v.code);

test("a complete order has no violations", () => {
  assert.deepEqual(codes(draft()), []);
});

test("delivery outside opening hours is rejected", () => {
  assert.deepEqual(checkDeliveryTime("2026-10-28T20:00", NOW).map((v) => v.code), ["outside_hours"]);
});

test("delivery with less than the minimum notice is rejected", () => {
  assert.deepEqual(checkDeliveryTime("2026-10-27T11:00", NOW).map((v) => v.code), ["too_soon"]);
});

test("delivery beyond the booking window is rejected", () => {
  assert.deepEqual(checkDeliveryTime("2026-12-20T10:00", NOW).map((v) => v.code), ["too_far"]);
});

test("an item id the model invented is a model fault", () => {
  const violations = checkDraft(draft({ items: [{ item_id: 99, quantity: 1 }] }), MENU, NOW);
  assert.ok(violations.some((v) => v.code === "unknown_item_id" && v.modelFault));
});

test("quantities outside 1-100 are rejected", () => {
  assert.ok(codes(draft({ items: [{ item_id: 1, quantity: 101 }] })).includes("bad_quantity"));
  assert.ok(codes(draft({ items: [{ item_id: 1, quantity: 0 }] })).includes("bad_quantity"));
});

test("off-menu requests are flagged, never invented", () => {
  assert.ok(codes(draft({ items: [], unmatched_items: ["chocolate cake"] })).includes("off_menu"));
});

test("a returning customer is offered their saved address", () => {
  const d = draft({ delivery_address: null });
  const message = clarificationFor(d, checkDraft(d, MENU, NOW), "Calle El Conde 105");
  assert.match(message, /Calle El Conde 105/);
});
