import assert from "node:assert/strict";
import { test } from "node:test";
import { MenuPrices, priceOrder } from "../src/services/orders.js";

const PRICES: MenuPrices = new Map([
  [1, { price: 650, item_name: "Tres Leches" }],
  [3, { price: 110, item_name: "Empanadas de queso" }],
  [7, { price: 0.1, item_name: "Test item" }],
]);

test("total is calculated from database prices", () => {
  const { total, lines, missing } = priceOrder([{ item_id: 1, quantity: 2 }, { item_id: 3, quantity: 12 }], PRICES);
  assert.equal(total, 2620);
  assert.deepEqual(missing, []);
  assert.deepEqual(lines.map((l) => l.unit_price), [650, 110]);
});

test("unknown items are reported and not priced", () => {
  const { total, missing } = priceOrder([{ item_id: 1, quantity: 1 }, { item_id: 42, quantity: 5 }], PRICES);
  assert.deepEqual(missing, [42]);
  assert.equal(total, 650);
});

test("totals have no floating point drift", () => {
  assert.equal(priceOrder([{ item_id: 7, quantity: 3 }], PRICES).total, 0.3);
});
