#!/usr/bin/env bash
# Pull the latest main and restart changed services. Run on the VPS (CI calls it over SSH).
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.prod.yml"

git pull --ff-only origin main
$COMPOSE up -d --build --remove-orphans --wait --wait-timeout 180
docker image prune -f >/dev/null
$COMPOSE ps --format "table {{.Service}}\t{{.Status}}"
