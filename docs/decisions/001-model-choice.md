# ADR 001: Model choice for order extraction

**Status:** accepted · 2026-10-27

## Context

`POST /llm/extract-order` turns a customer's Telegram message into structured order items. Every message costs one call, so cost per order matters, but a wrong item or invented dish costs a real customer. The model never sees or sets prices; totals are computed in `POST /orders` from Postgres.

## Measurement

30 eval cases in `evals/cases.jsonl`: normal orders, Spanish and mixed-language orders, missing details, off-menu items, an ambiguous item, delivery-time policy, quantity limits, and 5 prompt-injection attempts. Clock fixed at 2026-10-27 10:00 local. Full output in `evals/results.md`.

| Mode | Accuracy | Injection | Cost / order | Latency |
|---|---|---|---|---|
| claude-haiku-5-5 | 100% | 5/5 | $0.00020 | 1.6 s |
| claude-sonnet-5-5 | 100% | 5/5 | $0.00376 | 1.8 s |
| routed | 100% | 5/5 | $0.00020 | 1.6 s |

A first routing policy escalated whenever Haiku reported confidence below 0.6. That escalated 17% of cases, all of them messages that were legitimately incomplete ("2 tres leches please"), and raised the routed cost to $0.00082 with no accuracy gain.

## Decision

- Default to `claude-haiku-5-5` with structured outputs and `effort: low`.
- Escalate to `claude-sonnet-5-5` only when the fast model fails in a way code can detect: API error or refusal, an `item_id` not on the menu, a malformed `delivery_at`, or low confidence on a draft that would otherwise be accepted.
- Business rules (menu membership, quantities 1–100, delivery 08:00–18:00, at least 2 hours ahead, at most 14 days) live in `ops-api/src/rules.ts`, not in the prompt.

Sonnet costs about 19× more per order with no measured accuracy gain on this task, so it is kept as a safety net, not the default.

## Revisit when

- The eval set grows (real failed conversations get added as cases) and Haiku drops below 95%.
- The menu grows enough that the prompt crosses the prompt-caching threshold.
- Model prices change. Prices live in `PRICES_PER_MTOK` in `ops-api/src/llm/extract.ts`.
