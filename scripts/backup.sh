#!/usr/bin/env bash
# Nightly Postgres backup (app data + n8n), copied off-server when BACKUP_S3_BUCKET is set.
# Cron: 30 3 * * * /home/deploy/pastry-ops/scripts/backup.sh >> /home/deploy/backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

COMPOSE="docker compose -f ${COMPOSE_FILE_PATH:-docker-compose.prod.yml}"
BACKUP_DIR="$(pwd)/${BACKUP_DIR:-backups}"
mkdir -p "$BACKUP_DIR"

name="pastry-$(date -u +%Y%m%dT%H%M%SZ).dump"
$COMPOSE exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$BACKUP_DIR/$name.partial"
$COMPOSE exec -T postgres pg_restore --list < "$BACKUP_DIR/$name.partial" > /dev/null
mv "$BACKUP_DIR/$name.partial" "$BACKUP_DIR/$name"
find "$BACKUP_DIR" -name 'pastry-*.dump' -mtime +"${BACKUP_KEEP_LOCAL_DAYS:-7}" -delete

rclone() {
  docker run --rm -v "$BACKUP_DIR:/backups" \
    -e RCLONE_CONFIG_REMOTE_TYPE=s3 \
    -e RCLONE_CONFIG_REMOTE_PROVIDER="${BACKUP_S3_PROVIDER:-Other}" \
    -e RCLONE_CONFIG_REMOTE_ENDPOINT="${BACKUP_S3_ENDPOINT:-}" \
    -e RCLONE_CONFIG_REMOTE_ACCESS_KEY_ID="${BACKUP_S3_ACCESS_KEY_ID:-}" \
    -e RCLONE_CONFIG_REMOTE_SECRET_ACCESS_KEY="${BACKUP_S3_SECRET_ACCESS_KEY:-}" \
    rclone/rclone:1.68.2 "$@"
}

if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
  rclone copy "/backups/$name" "remote:$BACKUP_S3_BUCKET/"
  rclone delete --min-age "${BACKUP_KEEP_REMOTE_DAYS:-30}d" "remote:$BACKUP_S3_BUCKET/"
else
  echo "BACKUP_S3_BUCKET is not set: backup kept on this server only." >&2
fi

if [ -n "${BACKUP_HEARTBEAT_URL:-}" ]; then
  curl -fsS -m 10 "$BACKUP_HEARTBEAT_URL" > /dev/null
fi

echo "$(date -u +%FT%TZ) backup ok: $name ($(du -h "$BACKUP_DIR/$name" | cut -f1))"
