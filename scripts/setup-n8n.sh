#!/usr/bin/env bash
# One-time n8n setup on a new server: Telegram credential, workflows, activation, Telegram webhook.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

COMPOSE="docker compose -f ${COMPOSE_FILE_PATH:-docker-compose.prod.yml}"
n8n() { $COMPOSE exec -T n8n n8n "$@"; }

printf '[{"id":"pastryTelegramBot","name":"Pastry Telegram bot","type":"telegramApi","data":{"accessToken":"%s"}}]' "$TELEGRAM_BOT_TOKEN" \
  | $COMPOSE exec -T n8n sh -c 'cat > /tmp/credentials.json'
n8n import:credentials --input=/tmp/credentials.json
$COMPOSE exec -T n8n rm /tmp/credentials.json

for file in error-alert order-intake daily-prep-list pos-sync; do
  { printf '['; cat "n8n/workflows/$file.json"; printf ']'; } | $COMPOSE exec -T n8n sh -c "cat > /tmp/$file.json"
  n8n import:workflow --input="/tmp/$file.json"
done
n8n update:workflow --id=pastryOrderIntake --active=true
n8n update:workflow --id=pastryDailyPrepList --active=true
n8n update:workflow --id=pastryPosSync --active=true
$COMPOSE restart n8n

curl -fsS "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  --data-urlencode "url=https://$N8N_DOMAIN/webhook/order-intake" \
  --data-urlencode "secret_token=$TELEGRAM_WEBHOOK_SECRET" \
  --data-urlencode 'allowed_updates=["message"]'
echo
echo "n8n is ready at https://$N8N_DOMAIN"
