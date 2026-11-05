# Roadmap: POS → Shopify sync

The next layer of Pastry Ops. A Python service reads a point-of-sale (POS) database. Claude proposes how its columns map to Shopify fields, a person approves the map, and plain code then keeps Shopify's products, prices and stock in sync.

**The idea to sell:** the AI builds the map once, and the sync itself is plain code. It's cheap, tested, and every change can be traced.

The order bot stays in TypeScript. The sync is a new Python service in the same Docker Compose stack, so it reuses the existing VPS, Caddy, CI, backups and Telegram alerts.

---

## Phase 0: Finish Phase 2 go-live

Do this first, so reviewers have a live system to test.

- [ ] VPS, DNS and `.env` on the server ([runbook](docs/runbook.md) steps 1–5)
- [ ] Uptime Kuma monitors and the backup bucket (steps 6–7)
- [ ] GitHub secrets, and a push to `main` deploys (step 8)
- [ ] Go-live checks (step 9)

## Phase 1: Shopify setup

The step-by-step walkthrough is [below](#walkthrough-creating-the-dev-store-and-app).

- [x] Shopify account and organization in the Dev Dashboard
- [x] Development store `pastry-sync-demo` (USD, one location: "Shop location")
- [x] App `POS Sync` installed, with `write_products`, `write_inventory` and `read_locations`
- [x] Client ID and secret in `.env`; a token request works (API version `2026-10`)
- [ ] Shopify AI Toolkit installed in Claude Code
- [x] By hand with GraphQL: created a draft "Practice: Tres Leches" (SKU `PRACTICE-001`), set its stock, upserted it by handle, and read it back by SKU

**Done.** Two API rules that changed in 2026-04, which the sync must follow:
- `inventorySetQuantities` needs an `@idempotent(key: ...)` directive.
- Every quantity needs `changeFromQuantity`. A wrong value returns `CHANGE_FROM_QUANTITY_STALE`, which is a free conflict check.

## Phase 2: Mock POS database

A fake point-of-sale database for the pastry shop. It has no real client data, and it's built to be messy the way real POS data is.

- [x] `db/pos/mock_pos.sql`: a `pos.articulos` table with 38 products
  - Spanish column names: `codigo`, `descripcion`, `categoria`, `unidad`, `precio_venta`, `costo`, `existencia`, `itbis`, `activo`, `cod_barra`, `nota_interna`, `fecha_mod`
  - Prices stored as text in mixed formats (`$24.00`, `13.5`, `US$ 28.00`)
  - `S`/`N` flags; one lowercase, padded code (`'rep-006 '`); empty and missing barcodes
  - Drinks with fake stock of 999, internal-use rows, and a system row to ignore
- [x] A trigger updates `fecha_mod` on every edit, so the sync can find changed rows
- [x] `scripts/seed-pos.sh` loads it; running it again changes nothing

**Done.**

## Phase 3: Python sync service skeleton

- [x] `sync-api/`: FastAPI, Pydantic settings, httpx, psycopg 3, pytest and ruff, managed with uv. `anthropic` gets added in Phase 4, when it's first used.
- [x] Slim, non-root Docker image (`USER nobody`) with a health check, in both compose files
  - Port 3003 on the laptop, because ops-api uses 3002 there; 3002 inside Docker
  - Waits for ops-api, which runs the migrations
- [x] `db/migrations/005_sync.sql`: a `sync` schema with three tables
  - `sync.mappings`: proposed, approved or rejected
  - `sync.runs`
  - `sync.items`: SKU, hash of the last synced data, Shopify IDs
  - One database, one migration history: the existing ops-api runner applies it
- [x] Shopify client (`app/shopify.py`):
  - Client-credentials token, reused until a minute before it expires, and renewed after a 401
  - Retries:
    - `THROTTLED` (HTTP 200): waits until the cost bucket refills, at least 1 second
    - 429 and 5xx: exponential backoff
    - Gives up after 5 attempts
- [x] `GET /health` (checks the database) and `GET /shopify/shop` (needs the API key) to check the connection
- [x] 9 tests (7 Shopify client, 2 API); CI job `sync-api` runs ruff, pytest with Postgres and a Docker build, and deploys need it to pass

**Done.** `/health` is green in Docker; CI runs on the next push.

## Phase 4: AI mapping

- [ ] Read the POS table structure plus about 20 sample rows
- [ ] Claude returns a structured mapping, validated with Pydantic:
  - The Shopify target for each column (title, SKU, price, inventory, product type, unit option, or ignore)
  - The transform for each field (parse `RD$`, trim, upper-case codes, unit → variant option)
  - A confidence score and a one-line reason
- [ ] Low-confidence fields are flagged for review
- [ ] Approve or reject the mapping: Telegram buttons through n8n, or one endpoint
- [ ] Save as `mappings` version N. Only an approved version can sync.
- [ ] Reuse the daily budget guard and log calls to `llm_calls`
- [ ] Eval: 3 or 4 different POS layouts with known correct mappings; must score ≥ 90%

**Done when** a messy table produces a correct, approved mapping.

## Phase 5: Sync POS → Shopify (one direction)

- [ ] Apply the approved mapping to POS rows to build Shopify input. This is pure Python and unit-tested.
- [ ] Skip rows whose hash hasn't changed. Create or update products by SKU with `productSet`.
- [ ] Set stock with `inventorySetQuantities` at the store's main location
- [ ] Dry-run mode: return the planned changes without writing anything
- [ ] Every run records counts and errors in `sync_runs`
- [ ] Failures alert through the existing n8n error workflow
- [ ] n8n schedule: every 10–15 minutes, and `/sync` on Telegram for a manual run
- [ ] The first load handles about 40 products; note bulk operations as the path for large catalogs

**Done when** changing `existencia` in the POS shows up in Shopify within one cycle, and a second run changes nothing.

## Phase 6: Things reviewers can test

- [ ] **POS simulator:** a small page or a Telegram command that changes the price or stock of one product
- [ ] **Bring your own data:** upload a CSV export from any POS (max 1 MB, 500 rows) and get an AI mapping plus a dry-run preview. It never writes to Shopify.
- [ ] Storefront password and the steps to try it, in the README
- [ ] Add `sync.<domain>` to the Caddyfile and an Uptime Kuma monitor

## Phase 7: Docs, README and video

- [ ] README:
  - New Q&A section: "How does the Shopify sync work?"
  - Mermaid diagram
  - Python, FastAPI and Shopify in the stack table
- [ ] Manual pages for the sync layer, with swimlane flowcharts like the others
- [ ] About 3-minute video:
  1. The problem: the POS and Shopify drift apart
  2. Messy POS → AI mapping → approve
  3. First sync
  4. Change stock → Shopify updates by itself
  5. 15 seconds on the production side (CI, alerts, backups)
  6. What's next
- [ ] Update `resume.yaml` and `resume.tex` with the sync project bullets

## Later (mention it, don't build it)

- Orders flowing back: an `orders/create` webhook reduces stock in the POS
- Conflict rules when both sides change the same field
- Bulk operations for catalogs over 1,000 products
- A public app for many merchants, built with Shopify CLI and its template

---

## Walkthrough: creating the dev store and app

> As of Nov 2026, Shopify builds apps and dev stores in the **Dev Dashboard** (dev.shopify.com). The old "create a custom app in the store admin" option is legacy. If a screen doesn't match these steps, check the docs linked at the bottom.

### 1. Create the account

1. Go to **https://dev.shopify.com** and sign up. Use your personal email, so the store isn't tied to an employer.
2. When it asks, create an **organization** (for example "Grace Peralta Dev"). The org holds your apps and stores.

### 2. Create the development store

1. In the Dev Dashboard, open **Dev stores**, then **Add dev store** (or **Create store**).
2. Fill in the form:
   - **Name:** something like `pastry-sync-demo`. This becomes `pastry-sync-demo.myshopify.com`.
   - **Plan:** pick a standard plan (Basic or Grow) so every API behaves normally.
   - **Test data:** leave it off. The POS will provide the products.
3. Create the store, then open it. The store admin opens.
4. In the store admin:
   - **Settings → General:** set the store currency. USD is simpler for reviewers; DOP is true to the client story.
   - **Online Store → Preferences:** the storefront is password-protected. Note the password, since reviewers will need it.
   - **Settings → Locations:** note the default location name. Stock is set per location.

> **Important:** create the store **from the Dev Dashboard**, not from the Shopify admin. The token method below only works when the app and the store are in the same organization. Otherwise you get `shop_not_permitted`.

Development stores are free. Check the store page for any expiry date, so the demo doesn't stop while reviewers are looking.

### 3. Create the app

The wording below comes from Shopify's docs. The real screens may differ a little.

1. Click **Apps** in the left panel, then **Create app**, then **Start from Dev Dashboard**. Name it `POS Sync` and click **Create**.
2. On the **Versions** page, set **Scopes** to `write_products, write_inventory, read_locations`. Write access includes read access. Leave **App URL** as the default, then click **Release**.
   - A released version can't be edited. To change the scopes, release a new version.
3. Install the app on the store, from the app's **Installs** card or the **⋯** menu on the app's row in **Apps**.

### 4. Get the credentials

1. Open the app's **Settings** page and copy the **Client ID** and **Client secret**.
2. Put them in `.env`, never in git. Add placeholders to `.env.example`:
   ```
   SHOPIFY_STORE=pastry-sync-demo.myshopify.com
   SHOPIFY_CLIENT_ID=replace_me
   SHOPIFY_CLIENT_SECRET=replace_me
   SHOPIFY_API_VERSION=2026-10
   ```
3. Check that a token comes back:
   ```bash
   set -a; . ./.env; set +a
   curl -s -X POST "https://$SHOPIFY_STORE/admin/oauth/access_token" \
     -d grant_type=client_credentials \
     -d client_id="$SHOPIFY_CLIENT_ID" \
     -d client_secret="$SHOPIFY_CLIENT_SECRET"
   ```
   The response contains an `access_token` and `expires_in: 86399` (24 hours). The service will ask for a new token every day. The token never appears in the Shopify admin.
4. Make a first API call:
   ```bash
   curl -s "https://$SHOPIFY_STORE/admin/api/$SHOPIFY_API_VERSION/graphql.json" \
     -H "Content-Type: application/json" \
     -H "X-Shopify-Access-Token: <token from step 3>" \
     -d '{"query":"{ shop { name currencyCode } }"}'
   ```
   It should print your store's name.

### 5. Install the Shopify AI Toolkit in Claude Code

Requires Node 18 or newer.

```bash
claude mcp add --transport stdio shopify-dev-mcp -- npx -y @shopify/dev-mcp@latest
```

Restart Claude Code. The agent can now search Shopify's docs and check GraphQL against the live schema. The optional extra skills are installed with `npx skills add Shopify/shopify-ai-toolkit`.

### 6. Experiment in GraphiQL

In the store admin, install Shopify's **GraphiQL** app (search "Shopify GraphiQL App"), or use the API explorer on shopify.dev. Then try, in this order:

1. `productSet` to create "Tres Leches" with SKU `REP-010` and price 4.50
2. A `locations` query to get the location ID
3. `inventorySetQuantities` to set stock to 120
4. A `products` query to read it back
5. `productSet` again with the same SKU: it updates the product instead of creating a duplicate

This is the whole sync done by hand. Phase 5 just automates it.

### Docs

- Dev Dashboard tokens: https://shopify.dev/docs/apps/build/dev-dashboard/get-api-access-tokens
- AI Toolkit: https://shopify.dev/docs/apps/build/ai-toolkit
