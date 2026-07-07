#!/usr/bin/env bash
# Восстановить БД на Pi из дампа ноутбука. КЛЮЧЕВОЕ: пин версии TimescaleDB ровно как в дампе,
# иначе timescaledb_post_restore() падает с "catalog version mismatch". Образ timescaledb-ha
# бандлит много версий TS — создаём расширение нужной версии из чистого template0.
# Запуск НА Pi:  ./deploy/pi/db-restore.sh /tmp/geo.dump 2.27.1
set -euo pipefail
DUMP="${1:?путь к дампу (custom-format, pg_dump -Fc)}"
TSVER="${2:?версия timescaledb как в дампе, напр. 2.27.1}"
U="${GEO_DB_USER:-geo}"; D="${GEO_DB_NAME:-geoanalytics}"

echo "▶ пересоздаю $D из template0 + TS $TSVER + vector"
docker exec geo-db psql -U "$U" -d postgres -c "DROP DATABASE IF EXISTS $D WITH (FORCE);"
docker exec geo-db psql -U "$U" -d postgres -c "CREATE DATABASE $D TEMPLATE template0;"
docker exec geo-db psql -U "$U" -d "$D" -c "CREATE EXTENSION timescaledb VERSION '$TSVER';"
docker exec geo-db psql -U "$U" -d "$D" -c "CREATE EXTENSION vector;"

echo "▶ pre_restore → pg_restore → post_restore"
docker exec geo-db psql -U "$U" -d "$D" -tAc "SELECT timescaledb_pre_restore();" >/dev/null
docker exec -i geo-db pg_restore -U "$U" -d "$D" --no-owner --no-acl < "$DUMP" 2>/tmp/restore_err.txt || true
docker exec geo-db psql -U "$U" -d "$D" -tAc "SELECT timescaledb_post_restore();" >/dev/null
ERRS=$(grep -ciE "error:" /tmp/restore_err.txt || true)
echo "✓ restore готов (ошибок в логе: ${ERRS}); детали — /tmp/restore_err.txt"
