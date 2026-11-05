#!/usr/bin/env bash
# Restore a backup.
#   scripts/restore.sh backups/pastry-<time>.dump restore_test   # test restore into a scratch database
#   scripts/restore.sh backups/pastry-<time>.dump                # replace the live database (asks first)
#   scripts/restore.sh remote:pastry-<time>.dump restore_test    # download from object storage first
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

COMPOSE="docker compose -f ${COMPOSE_FILE_PATH:-docker-compose.prod.yml}"
BACKUP_DIR="$(pwd)/${BACKUP_DIR:-backups}"
source_file="${1:?usage: scripts/restore.sh <file.dump|remote:name.dump> [target_db]}"
target="${2:-$POSTGRES_DB}"
psql() { $COMPOSE exec -T postgres psql -U "$POSTGRES_USER" -v ON_ERROR_STOP=1 "$@"; }

if [[ "$source_file" == remote:* ]]; then
  name="${source_file#remote:}"
  docker run --rm -v "$BACKUP_DIR:/backups" \
    -e RCLONE_CONFIG_REMOTE_TYPE=s3 \
    -e RCLONE_CONFIG_REMOTE_PROVIDER="${BACKUP_S3_PROVIDER:-Other}" \
    -e RCLONE_CONFIG_REMOTE_ENDPOINT="${BACKUP_S3_ENDPOINT:-}" \
    -e RCLONE_CONFIG_REMOTE_ACCESS_KEY_ID="${BACKUP_S3_ACCESS_KEY_ID:-}" \
    -e RCLONE_CONFIG_REMOTE_SECRET_ACCESS_KEY="${BACKUP_S3_SECRET_ACCESS_KEY:-}" \
    rclone/rclone:1.68.2 copy "remote:$BACKUP_S3_BUCKET/$name" /backups/
  source_file="$BACKUP_DIR/$name"
fi
[ -f "$source_file" ] || { echo "Backup not found: $source_file" >&2; exit 1; }

live=false
if [ "$target" = "$POSTGRES_DB" ]; then
  live=true
  read -r -p "This REPLACES the live database '$target'. Type the database name to continue: " answer
  [ "$answer" = "$target" ] || { echo "Cancelled."; exit 1; }
  $COMPOSE stop n8n ops-api
fi

psql -d postgres -c "DROP DATABASE IF EXISTS \"$target\" WITH (FORCE)" -c "CREATE DATABASE \"$target\""
$COMPOSE exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$target" --no-owner --exit-on-error < "$source_file"

if $live; then
  $COMPOSE start ops-api n8n
fi

echo "Restored $(basename "$source_file") into '$target':"
psql -d "$target" -tA -c "SELECT 'menu items: ' || COUNT(*) FROM menu_items" \
  -c "SELECT 'orders: ' || COUNT(*) FROM orders" \
  -c "SELECT 'customers: ' || COUNT(*) FROM customers" \
  -c "SELECT 'n8n workflows: ' || COUNT(*) FROM workflow_entity"
