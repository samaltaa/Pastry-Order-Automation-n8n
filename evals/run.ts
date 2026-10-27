import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { extractOrder, Extraction, FAST_MODEL, STRONG_MODEL } from "../ops-api/src/llm/extract.js";
import { advance, emptyState } from "../ops-api/src/conversation.js";
import { MenuItem } from "../ops-api/src/rules.js";

type Case = {
  id: string;
  category: string;
  message?: string;
  turns?: string[];
  saved_address?: string;
  expect: {
    valid: boolean;
    items?: { item_id: number; quantity: number }[];
    delivery_at?: string;
    delivery_address_contains?: string;
  };
};

const here = dirname(fileURLToPath(import.meta.url));
const NOW = new Date("2026-10-27T10:00:00-04:00");
const CONCURRENCY = 5;

const MENU: MenuItem[] = [
  { id: 1, item_name: "Tres Leches", category: "cake", price: 650 },
  { id: 2, item_name: "Empanadas de carne", category: "savory", price: 120 },
  { id: 3, item_name: "Empanadas de queso", category: "savory", price: 110 },
  { id: 4, item_name: "Croissant de mantequilla", category: "bread", price: 180 },
  { id: 5, item_name: "Pan de queso", category: "bread", price: 140 },
  { id: 6, item_name: "Tarta de coco", category: "cake", price: 420 },
];

const cases: Case[] = readFileSync(join(here, "cases.jsonl"), "utf8")
  .split("\n")
  .filter(Boolean)
  .map((line) => JSON.parse(line));

const modes: { name: string; models: string[] }[] = [
  { name: `${FAST_MODEL} only`, models: [FAST_MODEL] },
  { name: `${STRONG_MODEL} only`, models: [STRONG_MODEL] },
  { name: `routed (${FAST_MODEL} → ${STRONG_MODEL})`, models: [FAST_MODEL, STRONG_MODEL] },
];

function itemsKey(items: { item_id: number; quantity: number }[]): string {
  return items.map((i) => `${i.item_id}x${i.quantity}`).sort().join(",");
}

function grade(c: Case, result: Extraction): string | null {
  if (!result.draft) return "no extraction";
  if (result.valid !== c.expect.valid) return `valid=${result.valid}, expected ${c.expect.valid} (${result.violations.map((v) => v.code).join(",") || "no violations"})`;
  if (!c.expect.valid) return null;
  if (c.expect.items && itemsKey(result.draft.items) !== itemsKey(c.expect.items)) {
    return `items ${itemsKey(result.draft.items)}, expected ${itemsKey(c.expect.items)}`;
  }
  if (c.expect.delivery_at && result.draft.delivery_at !== c.expect.delivery_at) {
    return `delivery_at ${result.draft.delivery_at}, expected ${c.expect.delivery_at}`;
  }
  if (c.expect.delivery_address_contains && !result.draft.delivery_address?.includes(c.expect.delivery_address_contains)) {
    return `address ${result.draft.delivery_address}, expected to contain ${c.expect.delivery_address_contains}`;
  }
  return null;
}

async function pool<T, R>(items: T[], size: number, fn: (item: T) => Promise<R>): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let next = 0;
  await Promise.all(
    Array.from({ length: size }, async () => {
      while (next < items.length) {
        const index = next++;
        results[index] = await fn(items[index]);
      }
    })
  );
  return results;
}

const report: string[] = [
  "# Extraction eval results",
  "",
  `${cases.length} cases · fixed clock ${NOW.toISOString()} · run ${new Date().toISOString().slice(0, 10)}`,
  "",
  "| Mode | Accuracy | Injection cases | Multi-turn cases | Avg cost / order | Avg latency | Escalation rate |",
  "|---|---|---|---|---|---|---|",
];
const failures: string[] = [];

for (const mode of modes) {
  const results = await pool(cases, CONCURRENCY, async (c) => {
    const turns = c.turns ?? [c.message ?? ""];
    let state = emptyState();
    const calls: Extraction["calls"] = [];
    let result!: Extraction;
    for (const turn of turns) {
      result = await extractOrder(turn, MENU, NOW, mode.models, { ...state, savedAddress: c.saved_address ?? null });
      calls.push(...result.calls);
      state = advance(state, turn, result);
    }
    result = { ...result, calls, escalated: calls.length > turns.length };
    return { c, result, failure: grade(c, result) };
  });
  const passed = results.filter((r) => !r.failure).length;
  const injection = results.filter((r) => r.c.category === "injection");
  const injectionPassed = injection.filter((r) => !r.failure).length;
  const cost = results.reduce((sum, r) => sum + r.result.calls.reduce((s, call) => s + call.costUsd, 0), 0);
  const latency = results.reduce((sum, r) => sum + r.result.calls.reduce((s, call) => s + call.latencyMs, 0), 0);
  const escalated = results.filter((r) => r.result.escalated).length;
  const multi = results.filter((r) => r.c.turns);
  const multiPassed = multi.filter((r) => !r.failure).length;

  const row = `| ${mode.name} | ${passed}/${cases.length} (${Math.round((passed / cases.length) * 100)}%) | ${injectionPassed}/${injection.length} | ${multiPassed}/${multi.length} | $${(cost / cases.length).toFixed(5)} | ${Math.round(latency / cases.length)} ms | ${Math.round((escalated / cases.length) * 100)}% |`;
  report.push(row);
  console.log(row);
  for (const r of results.filter((r) => r.failure)) {
    failures.push(`- **${mode.name}** \`${r.c.id}\`: ${r.failure}`);
  }
}

report.push("", "## Failures", "", ...(failures.length ? failures : ["None."]), "");
writeFileSync(join(here, "results.md"), report.join("\n"));
console.log(failures.join("\n"));
