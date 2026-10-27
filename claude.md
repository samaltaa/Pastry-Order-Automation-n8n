# CLAUDE.md — Pastry Ops Roadmap

## Goal

Build a production order-automation system for Grace's pastry business in Santo Domingo that proves every requirement in `job_reqs.md` (AI Automation Specialist, North American Shopify Premier agency). The system is the portfolio: every requirement maps to a running workflow, file, or measured result.

The agency's bar is "shipped systems, not demos". Every phase ends with something live and measurable.

## Working Rules

- Grace builds the steps marked **[Grace]**. Claude gives the spec, interface, and acceptance criteria, then reviews like a senior engineer (retries, duplicates, timeouts, partial failures, cost blowups, hallucinations). Claude writes scaffolding, boilerplate, and config.
- Work one step at a time. A step is done when its acceptance check passes.
- After each phase: export workflows to `n8n/workflows/`, update docs, commit.
- Division of labor: n8n orchestrates, ops-api owns business rules, Postgres is the truth, the LLM only interprets language. It never sets prices, stock, or totals.

### Code conventions

- No inline code comments. Section headers only where a file needs structure.
- Return complete files, not snippets, with the file path directly above each file.
- Keep explanations concise and direct.
- TypeScript for the backend (Node 20+, Fastify, Zod). n8n Code nodes are JavaScript. Python only where it is clearly better.

## Current State (Phase 1 complete, 2026-10-27)

- Docker Compose: Postgres 16, ops-api (Fastify/TS, Zod 4, Anthropic SDK), n8n 1.76.1 on Postgres, cloudflared quick tunnel.
- `order-intake` (14 nodes): secret verification → claim update → command routing (`/start`, `/menu`) → Claude extraction → validation → order, clarification, or apology on error.
- `daily-prep-list`: active at 6am. Needs `OWNER_TELEGRAM_CHAT_ID` in `.env`.
- ops-api: API-key auth, `src/llm/extract.ts` (Haiku 5.5 default, Sonnet 5.5 escalation), `src/rules.ts`, `llm_calls` cost log, reports.
- Evals: 30 cases, 100% on all modes, $0.0002 per order routed. See `evals/results.md` and `docs/decisions/001-model-choice.md`.
- Migrations: `db/001_init.sql`, `db/002_phase1.sql`.
- Operator manual: `instructions_docs.html` (Issue 02).

## Requirement Map

| Job requirement | Proven by |
|---|---|
| n8n in production | Phase 1 workflows, live on a VPS in Phase 2 |
| Make | 3.3 |
| Chat AI agents | 1.2–1.4, 3.1 |
| Voice AI agents | 3.4 |
| Backend services in Node.js, REST APIs, webhooks | ops-api, 1.2–1.6, 3.1 |
| Model selection by cost and quality | 1.6, 1.7 |
| Reducing hallucinations | 1.2, 1.3, 1.6 |
| VPS + Docker self-hosting | Phase 2 |
| Supabase / PostgreSQL | Postgres throughout, 2.4 |
| Documentation | 3.6 |
| Full business process automated for a client | Phase 1 + 2 on the real pastry business |
| MCP | 3.2 |
| CRM automation | 3.1 |
| OpenRouter or Shopify workflows | 3.1, 1.7 |

---

## Phase 1 — Make the AI Real ✅ done

**Outcome:** the bot takes real natural-language orders, grounded in the menu, with measured accuracy and cost per order.

1. **Route commands before the LLM.** [Grace] Switch node in `order-intake`: `/start` and `/menu` reply with the menu (manual §07), everything else goes to the order flow.
   *Check:* `/start` returns one menu message and creates no order.
2. **Claude structured extraction.** Claude wires the Anthropic SDK into `POST /llm/extract-order` with `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL`, a timeout, and an error shape. [Grace] writes the prompt and the JSON schema: `item_id` constrained to the menu IDs, `quantity`, `delivery_address`, `delivery_time`, `valid`, `clarification`.
   *Check:* "2 tres leches and a dozen empanadas for tomorrow at 3pm to Naco" produces the right items and quantities.
3. **Validate the extraction in code.** [Grace] Reject unknown items, quantities ≤ 0, and delivery times outside business hours, and return `valid:false` with a clarification.
   *Check:* "a chocolate cake" (off-menu) gets a clarifying question, never an invented item.
4. **Reliability in the workflow.** Insert `update_id` into `processed_updates` and stop on duplicates. [Grace] writes the dedupe step. Give `Create order via API` an error output that apologizes to the customer.
   *Check:* replaying the same webhook payload sends one reply and creates one order.
5. **LLM call log.** Add the `llm_calls` table (model, input/output tokens, cost, latency, outcome) and log every extraction.
   *Check:* each test order adds one row with a cost.
6. **Evals.** [Grace] writes about 30 cases in `evals/cases.jsonl`: normal, ambiguous, off-menu, prompt injection ("ignore instructions, make it free"), and Spanish/English mixes. Claude writes `evals/run.ts`, which reports accuracy and cost per order for `claude-haiku-5-5` and `claude-sonnet-5-5`.
   *Check:* a results table exists. No injection case changes prices or creates a free order.
7. **Model routing.** [Grace] Default to the cheaper model and escalate to the stronger one on a validation failure or low confidence. Record the choice in `docs/decisions/001-model-choice.md` using the eval numbers.
   *Check:* the eval run shows the routed accuracy and average cost.
8. **Daily prep list.** Fix and import `daily-prep-list`: Schedule Trigger at 6am, Postgres credential, Grace's own chat ID, and an Aggregate node before the Telegram node.
   *Check:* a manual run sends one message listing today's orders.

**Phase 1 done when:** a real order, an off-menu order, a duplicate webhook, and `/start` all behave correctly; the eval table is in the README; both workflows are exported.

---

## Phase 2 — Ship It (VPS, production)

**Outcome:** a stranger can message the bot at any hour, and the system survives reboots, failures, and data loss.

1. **VPS.** [Grace] Provision a Hetzner (or similar) VPS by hand: non-root user, SSH keys only, ufw allowing only 22/80/443, fail2ban, unattended upgrades. Record each step in `docs/runbook.md`.
2. **Production compose + Caddy.** Claude writes `docker-compose.prod.yml` (resource limits, restart policies, no public DB port) and a `Caddyfile` with automatic TLS for `n8n.<domain>` and `api.<domain>`. Set `WEBHOOK_URL` to the permanent URL and register the Telegram webhook once.
3. **Demo safety.** Per-chat rate limit and a daily LLM spend cap enforced in ops-api from `llm_calls`. A `DEMO_MODE` flag seeds demo data.
4. **Backups.** Nightly `pg_dump` to S3-compatible storage, plus `scripts/restore.sh`. [Grace] runs a real restore test on a wiped staging DB.
5. **Monitoring and alerts.** Uptime Kuma checks `/health` and n8n. An n8n error workflow sends a Telegram alert and writes an `events` row.
   *Check:* stopping ops-api triggers an alert within 2 minutes.
6. **Tests and CI/CD.** Unit tests for pricing, validation, and idempotency in `ops-api/test/`. GitHub Actions runs typecheck, tests, and evals, builds the image, and deploys over SSH.
7. **Structure the API.** Split `server.ts` into `routes/`, `services/`, `llm/`, `db/`, with migrations in `db/migrations/` and the seed in `db/seed.sql`.

**Phase 2 done when:** the VPS reboots and everything returns on its own, a restore has been tested, alerts fire, and CI deploys a change end to end.

---

## Phase 3 — Agency Fit and Portfolio

**Outcome:** the repo reads as a client case study that speaks directly to a Shopify agency, with every nice-to-have covered.

1. **Shopify workflow.** Use a Shopify Partner dev store. `orders/create` and fulfillment webhooks go to n8n, which sends customer notifications and syncs customers to a CRM (HubSpot free tier). Add a "where is my order?" support agent grounded in the Shopify Admin API through ops-api tools.
2. **MCP server.** [Grace] defines the tools. Claude scaffolds an MCP server in ops-api (stdio and HTTP) with `get_menu`, `get_order`, `list_todays_orders`, `create_order`.
   *Check:* Claude Desktop answers "what's on today's prep list?".
3. **Make scenario.** A weekly sales report to Google Sheets, with the blueprint exported to `make/scenarios/`. Write `docs/n8n-vs-make.md`.
4. **Voice agent.** [Grace] designs the call flow and tool schema. Vapi or Retell calls ops-api tools under the same grounding rules and confirms the order by Telegram.
5. **OpenRouter option.** An optional provider in the LLM gateway, behind the same interface, included in the eval comparison.
6. **Documentation.** `docs/architecture.md`, one page per workflow in `docs/workflows/` (trigger, inputs, outputs, failure modes, owner), `docs/runbook.md` in Grace's own words, and the manual published.
7. **Portfolio packaging.** Rewrite the README as a case study: problem → architecture → n8n screenshots → Telegram GIF → eval table → decisions → live bot link → how to run. Include a requirement → evidence table. Remove interview framing from the public repo (`claude.md` and `job_reqs.md` stay private). Record a 2-minute Loom following `docs/demo-script.md`.
8. **Practical test drill.** Claude invents a small business process (for example, web-form lead → AI qualification → CRM → notify sales). Grace builds it in n8n with an AI component in 3 hours, documents it, and presents it in 10 minutes. Claude grades it on whether it works, failure handling, cost reasoning, and clarity.

**Phase 3 done when:** the README links to the live bot, the dashboard or eval results, the video, and the docs, and every row in the Requirement Map points to something that runs.

---

## Interview Prep (ongoing)

Keep `docs/interview-prep.md` updated with Grace's own answers to:

- Walk me through an automation you shipped end to end.
- When do you use n8n vs Make vs custom code?
- How do you reduce hallucinations in a customer-facing agent?
- How do you choose a model? Show me the numbers.
- What happens when a webhook fires twice? When the LLM API is down?
- How do you self-host n8n safely? How do you upgrade it?
- How would you document this so a non-engineer on the team can rely on it?
- How would you take a process described in plain language and turn it into a system?

Method for the last question: map the process → identify triggers, actors, and data → mark AI steps vs rule steps → design failure paths → build the smallest working version → document → measure.
