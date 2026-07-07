#!/usr/bin/env bash
# Поднять Postgres (TimescaleDB+pgvector) на Raspberry Pi через `docker run` (без compose-плагина).
# Идемпотентно: если контейнер geo-db уже есть — просто стартует его. Запускать НА Pi из ~/News.
#   ./deploy/pi/db-up.sh
set -euo pipefail
cd "$(dirname "$0")/../.."

IMG="timescale/timescaledb-ha:pg16"

if docker ps -a --format '{{.Names}}' | grep -qx geo-db; then
  echo "▶ контейнер geo-db уже существует — стартую"
  docker start geo-db
else
  echo "▶ тяну образ ${IMG} (arm64)…"; docker pull "$IMG"
  echo "▶ запускаю geo-db…"
  docker run -d --name geo-db --restart unless-stopped \
    -e POSTGRES_USER="${GEO_DB_USER:-geo}" \
    -e POSTGRES_PASSWORD="${GEO_DB_PASSWORD:-geo}" \
    -e POSTGRES_DB="${GEO_DB_NAME:-geoanalytics}" \
    -p "${GEO_DB_PORT:-5432}:5432" \
    -v geo_db_data:/home/postgres/pgdata/data \
    -v "$PWD/scripts/db-init.sql:/docker-entrypoint-initdb.d/00-init.sql:ro" \
    "$IMG"
fi
echo "▶ жду готовности…"
for _ in $(seq 1 30); do
  docker exec geo-db pg_isready -U "${GEO_DB_USER:-geo}" -d "${GEO_DB_NAME:-geoanalytics}" >/dev/null 2>&1 && { echo "✓ БД готова."; exit 0; }
  sleep 2
done
echo "✗ БД не поднялась за 60с — docker logs geo-db"; exit 1
