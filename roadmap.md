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
- [x] Tests: Shopify client and API; CI job `sync-api` runs ruff, pytest with Postgres and a Docker build, and deploys need it to pass

**Done.** `/health` is green in Docker; CI runs on the next push.

## Phase 4: AI mapping

- [x] `app/pos.py`: reads column names and types plus up to 50 sample rows. Only tables in the `pos` schema can be read, so customer and order data never reach Claude.
- [x] `app/llm.py`: one Claude call (`claude-sonnet-5-5`) with structured output, checked against the Pydantic `Mapping` model. Claude returns:
  - The Shopify target for each column, chosen from a fixed list (title, sku, price, cost, quantity, product_type, barcode, taxable, status, ignore)
  - A transform for each column, chosen from a fixed list (money, integer, title_case, yes_no, active_status, inactive_status, ...)
  - A confidence score and a plain-English reason
  - The key column and any rows that aren't real products
- [x] `app/mapping.py`: approval checks (every column mapped once, no duplicate targets, title/sku/price present). Fields below 0.8 confidence are flagged for review. The transforms are plain Python.
- [x] Endpoints, all requiring the API key:
  - `POST /mappings`: propose a mapping and return problems, fields to review, and a 5-row preview of the Shopify output
  - `GET /mappings/{id}`
  - `POST /mappings/{id}/approve` (refused while there are problems) and `/reject`
- [x] Shares the daily AI budget with the bot: every call is logged to `llm_calls` as `sync/mapping`, and requests are refused once the budget is spent
- [x] Eval (`evals/run_mapping.py`): four layouts (pastry shop, Square export, hardware store with two price lists, legacy system with an inverted flag), 69 checks
  - **97.1%** on the first run, $0.06
  - The one miss: the legacy `DISC` (discontinued) column was ignored instead of being mapped to status
  - Runs in CI on `main`, and fails below 90%
- [x] 39 tests
- [ ] Approve or reject from Telegram (n8n buttons): moved to Phase 6

**Done.** Mapping #1 for `pos.articulos` has been proposed and is waiting for approval.

## Phase 5: Sync POS → Shopify (one direction)

- [x] `app/sync.py`: applies the approved mapping to every POS row, then for each product:
  - **New SKU:** `productSet` creates it with its stock, matched by a handle built from the SKU
  - **Product data changed** (title, price, cost, tax, status, barcode, category): `productSet` updates it, matched by Shopify ID
  - **Stock changed:** `inventorySetQuantities` with `@idempotent` and `changeFromQuantity` = the quantity this sync last set
  - **Nothing changed:** a hash of the last data sent (in `sync.items`) matches, and Shopify isn't called at all
- [x] Conflicts: if Shopify's stock moved since the last sync (an online sale), Shopify refuses with `CHANGE_FROM_QUANTITY_STALE`. The sync reads the current value, applies the POS quantity (the POS is the source of truth), and reports a conflict.
- [x] Rows missing a title, SKU or price fail on their own without stopping the run. Excluded rows (`REP-099`) are skipped.
- [x] Dry run (`{"dry_run": true}`): reports what would change and writes nothing
- [x] A Postgres advisory lock stops two runs overlapping (409)
- [x] Every run is recorded in `sync.runs` with counts: created, updated, stock updated, unchanged, failed, conflicts. `GET /sync/runs` lists them.
- [x] n8n workflow `pos-sync.json`:
  - Every 15 minutes, POST `/sync`
  - Telegram message to the owner when anything failed or conflicted
  - An HTTP error triggers the existing error-alert workflow
- [x] 7 sync tests against a fake Shopify that refuses stale stock updates like the real one (46 tests in total)
- [x] Tested on the real store:
  - First run created 37 products in about 29 seconds; the second run changed nothing
  - A price change caused 1 update; a stock change caused 1 stock call
  - A simulated online sale was reported as a conflict, and the POS value was applied
- [ ] Bulk operations for catalogs over 1,000 products, and reading only rows whose `fecha_mod` changed: mention in the video, not needed at 37 products
- [ ] `/sync` Telegram command: moved to Phase 6 with the other Telegram commands

**Done.**

## Phase 6: Things reviewers can test

- [x] **Demo page** (`/demo`, served by sync-api, on only with `DEMO_ENABLED=true`). Reviewers can:
  - Browse the products as photo cards, with search, category filters and their Shopify status
  - Change prices or stock (edits collect in a bottom bar), click **Sync to Shopify**, and open the product in the storefront
  - Upload their own CSV: Claude maps it, and they see a preview of the first rows as Shopify fields. Nothing is stored or synced.
  - See the approved mapping (with confidence and reasons) and the recent runs
- [x] Safety on the public routes:
  - Rate limits per visitor: 6 syncs/min, 3 CSV uploads/hour
  - Edits are sent with the sync (at most 50) and limited to the price (0.25–500) and stock (0–999) of existing products. One bad edit saves nothing.
  - CSV size limits: 200 KB and 40 columns; only the first 50 rows are sent to Claude
  - The page builds everything with `textContent`, so uploaded data can't inject HTML
  - The shared daily AI budget applies
- [x] **Telegram owner commands** (only `OWNER_TELEGRAM_CHAT_ID`): `/sync`, `/mapping`, `/approve N`, `/reject N`, `/runs`. The order workflow's router has a new branch that forwards them to `POST /commands`; the replies are formatted in Python and tested.
- [x] `app/service.py`: the API, the Telegram commands and the demo share one implementation of propose, review and sync
- [x] Caddy site `{$SYNC_DOMAIN}`: exposes only `/demo*` and `/health`, with a 300 KB request limit. The rest of sync-api stays internal.
- [x] Runbook step 9 (seed, `/mapping`, `/approve`, `/sync`), DNS record, `.env` values, Kuma monitor
- [x] Bug found with the demo's CSV preview and fixed: the money transform read `RD$ 20,00` as 2000. It now handles decimal commas (`1.250,50`) and thousands commas (`1,250`).
- [x] Products publish to the Online Store (`write_publications` scope), once per product
- [x] English products for North American customers, with photos from Wikimedia Commons (credits in `db/pos/IMAGE_CREDITS.md`). The mapping has an `image` target, and the sync sends a photo only when its URL changes, because Shopify re-uploads every image it's sent.
- [x] 68 tests; mapping eval at 97.1%
- [ ] On the VPS: depends on the Phase 0 go-live

**Done locally.** http://localhost:3003/demo

## Phase 6b: Google Sheets and Excel through Make

Many small businesses keep inventory in a spreadsheet, not a POS. The sheet becomes a second source for the same pipeline: Claude maps the columns, a person approves, and plain code syncs.

```
Google Sheet / Excel ──(Make)──▶ sync-api /sources/sheet ──▶ Postgres ──▶ Shopify
                                      │
                                      └─ headers changed? → Claude re-maps → Telegram asks to approve
```

**Who does what:**
- Make connects to the client's tools and handles the Google or Microsoft sign-in, with no OAuth code of our own.
- sync-api holds all the logic: mapping, validation, sync and conflicts, which keeps it tested and evaluated.
- n8n stays on internal ops: the schedule, Telegram and alerts.
- Make has no transformation logic. Its scenario blueprints are exported into `make/`.

**Decisions:**
- **Send the whole sheet each time.** On every change, Make posts all the rows. The hash check keeps unchanged rows free. Deleted rows and events arriving out of order stop being problems.
- **Removed rows go to Draft.** A product missing from the sheet becomes Draft in Shopify, never deleted, so the change is reversible.
- **One owner per SKU.** Every SKU belongs to one source. If the same SKU appears in two sources, it's flagged and skipped.
  - Demo story: the register handles the counter items. The owner keeps a "Catering & Holiday Menu" sheet with its own SKUs.
- **Header drift holds the sync.** If the columns no longer match the approved mapping, nothing syncs. Claude proposes a new mapping, and Telegram asks for `/approve N`.
- **Bad cells fail per row.** One unreadable value fails its row only, never the whole run.
- **Live means seconds.** Use Make's instant Google Sheets trigger rather than polling. Check the module names, plan limits and operation costs in Make's docs before building.

**Steps:**
- [x] Make account created
- [x] Ingest endpoint `POST /sources/sheets/{name}`:
  - Protected by its own key (`SHEET_INGEST_KEY`), not the master API key
  - Saves each snapshot as `sheets.<name>`, recreated when the headers change
  - Mapping and sync read it like a POS table
  - `SYNC_SOURCE` picks the one active source (POS table or sheet), so they never overwrite each other
  - Rows removed from the source go to Draft; if more than half would go at once, nothing is removed
  - `/approve` in Telegram syncs straight away
  - 15 new tests (83 in total)
- [x] Caddy: expose the ingest endpoint (API key, body size limit) so Make's cloud can reach it. Locally this goes through the public tunnel.
- [x] The inventory sheet: `make/inventory.csv`, all 37 products with photos, imported into Google Sheets
- [ ] Google Sheets Make scenario: watch changes → read all rows → POST to sync-api. Commit the blueprint. (Watch Changes works.)
- [ ] Write-back Make scenario: custom webhook → find the row by SKU → update price and stock. Commit the blueprint.
- [x] Header drift: hold the sync, re-propose the mapping, send the Telegram approval message (Claude is asked once per change of columns)
- [x] Write-back: demo edits on the sheet source go to a Make webhook (`MAKE_SHEET_WEBHOOK_URL`), and the page reports whether the sheet was updated
- [ ] Demo:
  - A shared "Catering & Holiday Menu" sheet that anyone with the link can edit, with the header row protected
  - A "Live from Google Sheets" panel on `/demo`
  - Same limits as the demo page
- [ ] Runbook and `.env.example` entries
- [ ] Stretch: Make writes a "Sync status" column back into the sheet
  - Example values: "✓ 3:02 pm", "Price unreadable"
  - Written only when a row's status changes, so the write-back can't trigger a loop
- [ ] Stretch: Excel (Microsoft 365) scenario posting to the same endpoint. Needs a Microsoft account.

**Known gap (say it in the video):** online sales don't flow back to the sheet. The next sheet sync detects the conflict, applies the sheet's number and alerts the owner, the same "source wins" rule as the POS.

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
  5. Split screen: edit the Google Sheet → the storefront updates; rename a column → Telegram asks to approve
  6. 15 seconds on the production side (CI, alerts, backups)
  7. What's next
- [ ] Update `resume.yaml` and `resume.tex` with the sync project bullets

## Later (mention it, don't build it)

- Orders flowing back: an `orders/create` webhook reduces stock in the POS and in the sheet
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
