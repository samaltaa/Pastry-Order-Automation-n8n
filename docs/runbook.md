# Production runbook

How to put Pastry Ops on a server, and how to run it once it's there. Follow the steps in order. Each one ends with a check.

## What you need

| Item | Suggestion | Cost |
|---|---|---|
| VPS | Hetzner CX22, Ubuntu 24.04, 2 vCPU, 4 GB RAM | about €5/month |
| Domain | Any registrar, for example `pastryops.dev` | about $10/year |
| Object storage for backups | Cloudflare R2 or Backblaze B2 bucket | free at this size |
| An SSH key on your laptop | `ssh-keygen -t ed25519` if you don't have one | free |

## 1. DNS

At your registrar, create two `A` records pointing to the server's IP address:

```
n8n.<your-domain>     A   <server-ip>
status.<your-domain>  A   <server-ip>
sync.<your-domain>    A   <server-ip>
```

*Check:* `dig +short n8n.<your-domain>` prints the server IP.

## 2. Harden the server

When you create the VPS, add your SSH public key. Then, from your laptop:

```bash
scp deploy/provision.sh root@<server-ip>:
ssh root@<server-ip> bash provision.sh
```

The script creates a `deploy` user with your key, turns off root and password login, allows only ports 22, 80 and 443 (ufw), bans repeated failed logins (fail2ban), turns on automatic security updates, adds 2 GB of swap, and installs Docker.

*Check:* **before closing the root session**, open a new terminal and run `ssh deploy@<server-ip>`. Then confirm `ssh root@<server-ip>` is refused.

## 3. Get the code and the settings

```bash
ssh deploy@<server-ip>
git clone https://github.com/samaltaa/Pastry-Order-Automation-n8n.git pastry-ops
cd pastry-ops
cp .env.example .env
chmod 600 .env
nano .env
```

Fill in every `replace_me` value. Generate new secrets on the server; don't reuse the laptop ones:

| Variable | Value |
|---|---|
| `POSTGRES_PASSWORD` | `openssl rand -hex 24`. Use the same value inside `DATABASE_URL`. |
| `N8N_ENCRYPTION_KEY` | `openssl rand -hex 32`. Never change it after the first start. |
| `OPS_API_KEY` | `openssl rand -hex 32` |
| `TELEGRAM_WEBHOOK_SECRET` | `openssl rand -hex 16` |
| `TELEGRAM_BOT_TOKEN`, `OWNER_TELEGRAM_CHAT_ID`, `ANTHROPIC_API_KEY` | Same as on your laptop |
| `N8N_DOMAIN`, `STATUS_DOMAIN`, `ACME_EMAIL` | `n8n.<your-domain>`, `status.<your-domain>`, your email |
| `BACKUP_S3_*` | From your R2 or B2 bucket (step 7) |
| `SHOPIFY_STORE`, `SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET` | Same as on your laptop (see [roadmap.md](../roadmap.md), Phase 1) |
| `SYNC_DOMAIN`, `DEMO_ENABLED`, `STOREFRONT_URL`, `STOREFRONT_PASSWORD` | `sync.<your-domain>`, `true` to open the reviewer demo, the store URL and its storefront password |
| `DAILY_LLM_BUDGET_USD`, `CHAT_RATE_LIMIT_PER_MINUTE` | Defaults are $2 and 6 |

## 4. Start the stack

```bash
docker compose -f docker-compose.prod.yml up -d --build --wait
docker compose -f docker-compose.prod.yml ps
```

*Check:* all six services show `healthy` or `Up`, and `https://n8n.<your-domain>` opens with a valid certificate. The first certificate can take a minute.

## 5. Set up n8n and the bot

Stop the laptop stack first, so two copies don't answer the same bot:

```bash
docker compose down          # on your laptop
```

Then on the server:

```bash
scripts/setup-n8n.sh
```

This imports the Telegram credential and all four workflows, activates them, and points the Telegram webhook at `https://n8n.<your-domain>/webhook/order-intake`.

Open `https://n8n.<your-domain>` and create the n8n owner account straight away.

*Check:* send `/menu` to the bot. It replies with the menu. Then place a test order.

## 6. Monitoring

Open `https://status.<your-domain>` and create the admin account. Then:

1. **Settings → Notifications → Setup Notification → Telegram.** Use the bot token and your `OWNER_TELEGRAM_CHAT_ID`. Tick "Default enabled".
2. **Add monitor → HTTP(s):** name `ops-api`, URL `http://ops-api:3001/health`, interval 60 s, retries 1.
3. **Add monitor → HTTP(s):** name `n8n`, URL `https://n8n.<your-domain>/healthz`, interval 60 s, retries 1.
4. **Add monitor → HTTP(s):** name `sync-api`, URL `http://sync-api:3002/health`, interval 60 s, retries 1.
5. **Add monitor → Push:** name `Nightly backup`, heartbeat interval 90000 seconds (25 hours). Copy the push URL into `BACKUP_HEARTBEAT_URL` in `.env`.

*Check:* `docker compose -f docker-compose.prod.yml stop ops-api`. A Telegram alert arrives within 2 minutes. Then `start ops-api` and a "back up" message follows.

## 7. Backups

Create a bucket (for example `pastry-ops-backups`) and an API token that can read and write it. Fill in `BACKUP_S3_ENDPOINT`, `BACKUP_S3_BUCKET`, `BACKUP_S3_ACCESS_KEY_ID` and `BACKUP_S3_SECRET_ACCESS_KEY`. For Backblaze B2, set `BACKUP_S3_PROVIDER=Other`.

```bash
scripts/backup.sh
crontab -e
```

Add this line:

```
30 3 * * * /home/deploy/pastry-ops/scripts/backup.sh >> /home/deploy/backup.log 2>&1
```

**Restore test (do this once before going live):**

```bash
scripts/restore.sh remote:<file-name>.dump restore_test
docker compose -f docker-compose.prod.yml exec postgres psql -U pastry -d postgres -c "DROP DATABASE restore_test"
```

*Check:* the restore prints the menu, order and workflow counts, and the file is visible in the bucket.

## 8. CI/CD

On your laptop, create a key used only by GitHub:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/pastry-deploy -N "" -C github-deploy
ssh-copy-id -i ~/.ssh/pastry-deploy.pub deploy@<server-ip>
ssh-keyscan <server-ip>
```

In GitHub, open the repo's **Settings → Environments**, create one called `production`, and add these secrets. Add `ANTHROPIC_API_KEY` under **Settings → Secrets and variables → Actions** too, so the evals job can see it.

| Secret | Value |
|---|---|
| `VPS_HOST` | the server IP |
| `VPS_USER` | `deploy` |
| `VPS_SSH_KEY` | contents of `~/.ssh/pastry-deploy` (the private key) |
| `VPS_KNOWN_HOSTS` | the output of `ssh-keyscan` |
| `ANTHROPIC_API_KEY` | your key. The eval step costs about $0.01 per run. |

*Check:* push a small change to `main`. The Actions tab shows test → evals → deploy all green, and `docker compose -f docker-compose.prod.yml ps` on the server shows fresh containers.

## 9. POS → Shopify sync

Load the mock POS, then have Claude propose a mapping and approve it from Telegram:

```bash
export COMPOSE_FILE=docker-compose.prod.yml
scripts/seed-pos.sh
```

Send these to the bot from your own Telegram account (only `OWNER_TELEGRAM_CHAT_ID` can use them):

1. `/mapping`: Claude reads `pos.articulos` and replies with its proposal and an id.
2. `/approve <id>`
3. `/sync`: the first run creates every product in Shopify (about 30 seconds).

After that, the n8n schedule syncs every 15 minutes and messages you only when something fails or a stock conflict is found. `/runs` lists recent runs.

*Check:* `https://sync.<your-domain>` opens the demo page. Change a price there, click **Sync now**, and the storefront shows the new price.

## 10. Go-live checks

- [ ] `sudo reboot`, wait 2 minutes: the bot answers again without you doing anything.
- [ ] A restore from the bucket worked (step 7).
- [ ] Stopping ops-api sent an alert (step 6).
- [ ] A push to `main` deployed on its own (step 8).
- [ ] `/sync` from Telegram answers, and the demo page shows every product as "in Shopify" (step 9).

## Day to day

```bash
cd ~/pastry-ops
export COMPOSE_FILE=docker-compose.prod.yml
docker compose ps
docker compose logs -f ops-api
docker compose logs -f n8n
```

| Task | How |
|---|---|
| Deploy by hand | `scripts/deploy.sh` |
| See AI spend today | `docker compose exec ops-api wget -qO- --header "x-api-key: $OPS_API_KEY" http://127.0.0.1:3001/reports/llm-usage` (after `set -a; . ./.env; set +a`) |
| See recent alerts and events | same, with `/reports/events` |
| Change a workflow | Edit in the n8n editor, then **Download** and commit the JSON to `n8n/workflows/` |
| Change the menu | `docker compose exec postgres psql -U pastry -d pastry_ops` and update `menu_items` |
| Upgrade n8n | Change the image tag in `docker-compose.prod.yml`, run `scripts/backup.sh`, then `scripts/deploy.sh` |
| Restore the live database | `scripts/restore.sh backups/<file>.dump` (asks you to type the database name) |
