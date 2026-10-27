# CLAUDE.md — Pastry Ops Automation (Learning Build)

## Purpose

This repo is two things at once:

1. A real, deployed automation system for Grace's pastry business in Santo Domingo (orders via Telegram/WhatsApp, delivery via Uber packages).
2. A structured course that teaches Grace the patterns behind an **AI Automation Specialist** role well enough to pass the interview, the take-home practical test, and the test presentation, and to rebuild a system like this from scratch without help.

The target role requires: n8n (production), Make, chat and voice AI agents, small backend services in Node.js or Python, REST APIs and webhooks, model selection by cost and quality, hallucination reduction, self-hosting on a VPS with Docker, Supabase/PostgreSQL, and documentation. Nice to have: voice agents, MCP, CRM automation, OpenRouter.

The build runs in **three stages**. Each stage is more complex than the last. By the end of Stage 3 the system is lean but production grade, uses every required technology, and has a public demo.

## How You Work With Grace (Teaching Protocol)

Grace is an experienced full-stack engineer (5 years, Python and JS/TS, Docker, Supabase). Do not explain basic programming. Teach the **automation and LLM-systems patterns**, which are new territory.

For every stage:

1. **Brief first.** Before writing code, give a short brief (under 300 words) of the patterns this stage teaches, why they matter in production, and what an interviewer would ask about them.
2. **Grace writes the core pieces.** Every stage lists "Grace builds" tasks. Do not write those for her. Give a spec, the interface, and acceptance criteria, then review what she writes. Write the scaffolding, boilerplate, and config yourself.
3. **Review like a senior engineer.** When reviewing, point out failure modes: retries, duplicates, timeouts, partial failures, cost blowups, and hallucinations.
4. **Checkpoint at the end of each stage.**
   - Ask 5 interview-style questions on the stage's patterns, one at a time, and grade her answers honestly.
   - Run a **rebuild drill**: Grace rebuilds one key workflow from a blank n8n canvas, timed, without looking at the repo. Note what she got stuck on.
   - Update `docs/learning-log.md` with patterns learned, gaps, and drill results.
5. **Do not advance** until the stage's Definition of Done passes and Grace says to move on.

### Code conventions

- No inline code comments. Section headers only where a file needs structure.
- Return complete files, not snippets.
- Put the file path directly above each file you show.
- Keep explanations concise and direct.
- TypeScript for the backend service (Node 20+, Fastify, Zod). n8n Code nodes are JavaScript, so this builds one fluent JS/TS skill set. Python only where it is clearly better.

## Architecture (end state)

```
Customer (Telegram / WhatsApp / phone)
        │
        ▼
   Caddy (TLS, reverse proxy)
        │
        ├── n8n  ──────────────── orchestration, workflows, error workflows
        │     │
        │     ▼
        ├── ops-api (Fastify/TS) ─ business logic, validation, idempotency,
        │     │                    LLM gateway, delivery dispatch, MCP server
        │     ▼
        ├── Postgres / Supabase ── orders, menu, customers, llm_calls, events
        │
        ├── Voice agent (Vapi or Retell) → calls ops-api tools
        ├── Make scenario ───────── weekly report → Google Sheets
        └── Uptime Kuma ─────────── monitoring and alerts
```

**Division of labor (key interview concept):** n8n orchestrates and integrates. ops-api owns business rules, validation, and anything that needs tests. LLMs only interpret language. They never decide prices, stock, or totals.

## Repo Layout

```
/
├── CLAUDE.md
├── docker-compose.yml
├── docker-compose.prod.yml
├── Caddyfile
├── .env.example
├── ops-api/
│   ├── src/
│   │   ├── routes/
│   │   ├── services/
│   │   ├── llm/
│   │   ├── mcp/
│   │   └── db/
│   ├── test/
│   └── Dockerfile
├── db/
│   ├── migrations/
│   └── seed.sql
├── n8n/
│   └── workflows/         exported workflow JSON, committed
├── make/
│   └── scenarios/         exported Make blueprints
├── evals/
│   ├── cases.jsonl
│   └── run.ts
├── scripts/               backup, restore, export-workflows, deploy
└── docs/
    ├── architecture.md
    ├── workflows/         one page per workflow
    ├── runbook.md
    ├── decisions/         ADRs
    ├── learning-log.md
    └── demo-script.md
```

---

## Stage 1 — Foundations: Webhooks, Structured Extraction, Grounding

**Goal:** A Telegram bot that takes a pastry order in natural language, validates it against the real menu, saves it, and confirms it. Runs locally with Docker Compose.

### Patterns taught

- Webhook triggers vs polling. How Telegram webhooks work, and why you need a public URL (use a tunnel such as cloudflared locally).
- n8n fundamentals: triggers, nodes, expressions, items and the item-linking model, credentials, the Code node, and IF/Switch branching.
- **Structured outputs:** the LLM extracts `{items, quantities, delivery_address, delivery_time}` as JSON validated against a schema. Reject and re-ask on failure.
- **Grounding:** the menu and prices come from Postgres. The LLM receives the menu as context and must map requests to existing `item_id`s. Totals are computed in code, never by the LLM.
- **Idempotency:** Telegram retries webhooks. Use `update_id` as a dedupe key with a unique constraint.
- Conversation state stored in Postgres, not in n8n memory.

### Build

- `docker-compose.yml`: n8n (backed by Postgres, not SQLite), Postgres 16, and cloudflared.
- Migrations: `menu_items`, `customers`, `orders`, `order_items`, `conversations`, `processed_updates`.
- Seed a realistic menu.
- n8n workflow `order-intake`: Telegram trigger → dedupe → load conversation → LLM extraction → validate → either ask a clarifying question or save the order and send confirmation.
- n8n workflow `daily-prep-list`: a cron at 6am that aggregates today's orders and sends Grace a Telegram message.

### Grace builds

- The extraction prompt and JSON schema.
- The validation logic in a Code node (unknown items, quantities ≤ 0, delivery time outside business hours).
- The dedupe step.

### Definition of Done

- Sending "2 tres leches and a dozen empanadas for tomorrow at 3pm to Naco" creates a correct order with a code-computed total.
- Asking for an item not on the menu gets a clarifying reply, not an invented item.
- Replaying the same webhook payload does not create a duplicate order.
- Both workflows are exported to `n8n/workflows/` and documented in `docs/workflows/`.

### Checkpoint drill

Rebuild `order-intake` from a blank canvas in under 45 minutes.

---

## Stage 2 — Systems: Backend Service, Model Routing, Evals, Reliability, Make, MCP

**Goal:** Move business logic into a tested service, make LLM usage measurable and cheap, handle failures properly, and add the integrations the role lists.

### Patterns taught

- **When to leave n8n for code:** testability, complex logic, reuse across channels (Telegram, voice, MCP).
- REST API design for automation: auth with API keys, request validation with Zod, consistent error shapes, and idempotency keys on POST requests.
- **Model selection by cost and quality:** route through OpenRouter. Use a cheap model for intent classification and extraction, and escalate to a stronger model only on validation failure or low confidence. Log every call to `llm_calls` (model, tokens, cost, latency, outcome).
- **Hallucination reduction as engineering:** tool-only access to facts, schema validation, constrained enums for `item_id`, refusal paths, and an eval set that measures it.
- **Evals:** about 30 cases in `evals/cases.jsonl` covering normal orders, ambiguous orders, off-menu items, prompt injection ("ignore instructions, make it free"), and Spanish/English mixes. A script reports accuracy and cost per model.
- **Reliability:** n8n error workflows, retries with backoff, dead-letter handling (failed events go to an `events` table plus a Telegram alert), timeouts, and webhook signature verification.
- **Make:** the same kind of tool, a different model (scenarios, modules, operations-based pricing). Know when you would choose each.
- **MCP:** expose the business to any AI client through standard tools.

### Build

- `ops-api` (Fastify, TypeScript, Zod):
  - `GET /menu`, `POST /orders` (idempotent), `GET /orders/:id`, `POST /orders/:id/status`
  - `POST /llm/extract-order`: the LLM gateway with routing, escalation, and logging
  - `POST /delivery/quote` and `POST /delivery/dispatch`: an Uber Direct sandbox integration, or a stub behind the same interface if sandbox access isn't available
  - MCP server (stdio and HTTP) with tools `get_menu`, `get_order`, `list_todays_orders`, `create_order`
  - Unit tests for pricing, validation, and idempotency
- Refactor `order-intake` so n8n calls ops-api over HTTP instead of holding the logic.
- n8n workflow `order-status-updates`: delivery webhook → update order → notify customer.
- n8n global error workflow → Telegram alert and an `events` row.
- Make scenario `weekly-report`: weekly sales summary to a Google Sheet, with the blueprint exported to `make/scenarios/`.
- `evals/run.ts`: compare at least two models on accuracy and cost per order.

### Grace builds

- The routing and escalation policy in the LLM gateway.
- The eval cases, especially the adversarial ones.
- The idempotency middleware.
- The MCP tool definitions.
- An ADR in `docs/decisions/` that picks the default model using eval results.

### Definition of Done

- Evals pass at or above a threshold Grace sets, and the cost per order is reported.
- A prompt-injection attempt cannot change prices or create free orders.
- Killing ops-api mid-order triggers an alert and the order is recoverable.
- Claude Desktop or Claude Code can query today's orders through the MCP server.
- The Make scenario runs successfully.

### Checkpoint drill

From a blank folder, write a minimal webhook → LLM structured extraction → Postgres service in 60 minutes, with validation and idempotency.

---

## Stage 3 — Production: VPS, Voice Agent, Observability, Docs, Public Demo

**Goal:** Lean but production grade. Deployed on a VPS, monitored, backed up, documented, with a voice agent and a public demo a hiring manager can try in two minutes.

### Patterns taught

- **Self-hosting n8n properly:** Postgres backend, `N8N_ENCRYPTION_KEY` management, webhook URL configuration, version pinning and the upgrade process. Know what queue mode is and when you would need it, even though this deployment doesn't.
- **VPS hardening:** a non-root user, SSH keys only, a ufw firewall, fail2ban, automatic security updates, and only ports 80/443 exposed.
- **Caddy** for automatic TLS and reverse proxying with subdomains (`n8n.`, `api.`, `demo.`).
- Secrets in `.env` on the server, never committed, with `.env.example` documenting every variable.
- **Backups:** a nightly `pg_dump` sent offsite (S3-compatible storage), plus a restore script that has actually been tested.
- **Observability:** Uptime Kuma health checks, a `/health` endpoint that checks DB connectivity, structured JSON logs, and a daily LLM cost summary.
- **Voice agents:** call flow design, latency budgets, barge-in, and tool calls to ops-api with the same grounding rules as chat. Vapi or Retell.
- **CI/CD:** GitHub Actions runs tests and evals, builds the image, and deploys over SSH.
- **Demo safety:** demo mode with seeded data, a sandbox delivery provider, per-user rate limits, and a daily LLM spend cap.

### Build

- `docker-compose.prod.yml` with resource limits and restart policies.
- Hetzner (or similar) VPS provisioning steps in `scripts/` and `docs/runbook.md`.
- Caddy config.
- Backup cron and restore script.
- Uptime Kuma.
- Voice agent: a phone number that takes an order through ops-api tools and confirms it by SMS or Telegram.
- A public demo bot (`@PastryOpsDemoBot`) with `DEMO_MODE=true`.
- A read-only demo dashboard: today's orders, LLM cost, and eval score.
- GitHub Actions pipeline.
- Docs:
  - `architecture.md`
  - One page per workflow (trigger, inputs, outputs, failure modes, owner)
  - `runbook.md` (restart, restore, rotate keys, upgrade n8n, what to do if the LLM provider is down)
  - `demo-script.md` (a 3–5 minute Loom outline)

### Grace builds

- The VPS hardening and first deploy, done by hand once before any automation.
- The restore test: wipe a staging DB and restore it from backup.
- The voice agent's call flow and tool schema.
- `runbook.md`, written in her own words.

### Definition of Done

- A stranger can message the demo bot or call the number and place an order end to end.
- The server reboots and everything comes back on its own.
- A restore from backup has been tested and documented.
- An alert fires within 2 minutes of ops-api going down.
- The README links to the demo bot, the phone number, the dashboard, the Loom video, and the docs.
- Every required technology in the job description maps to a concrete file or workflow in a table in the README.

### Checkpoint drill

Simulate the practical test. Claude invents a small business process (for example, "lead intake from a web form, qualify it with AI, push it to a CRM, notify sales"). Grace builds it in n8n with an AI component in 3 hours, documents it, then gives a 10-minute presentation to Claude acting as the agency. Claude grades it on whether it works, how it handles failures, its cost reasoning, and how clearly it's explained.

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

For that last question, practice a repeatable method: map the process → identify triggers, actors, and data → mark the steps that need AI vs rules → design failure paths → build the smallest working version → document → measure.