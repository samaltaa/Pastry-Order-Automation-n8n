#!/usr/bin/env bash
# Creates and fills the mock POS schema (pos.articulos). Safe to run more than once.
# Uses docker-compose.yml by default; on the server, export COMPOSE_FILE=docker-compose.prod.yml first.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" < db/pos/mock_pos.sql
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "SELECT categoria, COUNT(*) AS articulos, SUM(existencia) AS existencia FROM pos.articulos GROUP BY categoria ORDER BY categoria"
