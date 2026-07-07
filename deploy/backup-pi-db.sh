#!/usr/bin/env bash
# Ночной бэкап БД с Raspberry Pi НА НОУТ. Pi-БД — единственная живая копия на SD-карте, поэтому
# тянем её off-device. Онлайновый pg_dump через SSH (без даунтайма Pi). Ротация: последние KEEP.
# Запускается systemd-таймером geo-backup.timer (00:00, Persistent → догон после простоя ноута).
#
#   ./deploy/backup-pi-db.sh            # разовый бэкап
set -euo pipefail

PI_HOST="${PI_HOST:-pi@192.168.0.114}"
DEST="${GEO_BACKUP_DIR:-$HOME/geo-backups}"
KEEP="${GEO_BACKUP_KEEP:-14}"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=10"

mkdir -p "$DEST"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$DEST/geo-$STAMP.dump"

# Версия TimescaleDB — нужна при восстановлении (db-restore.sh пинит её, иначе post_restore падает).
TSVER="$($SSH "$PI_HOST" "docker exec geo-db psql -U geo -d geoanalytics -tAc \"select extversion from pg_extension where extname='timescaledb'\"" 2>/dev/null | tr -d '[:space:]' || true)"

# Онлайновый дамп Pi → файл на ноуте (через .partial, чтобы прерванный дамп не считался валидным).
if ! $SSH "$PI_HOST" "docker exec geo-db pg_dump -U geo -Fc geoanalytics" > "$OUT.partial" 2>/dev/null; then
  echo "✗ $(date '+%F %T') дамп не снят (Pi недоступна?) — старые бэкапы не трогаю"; rm -f "$OUT.partial"; exit 1
fi
SZ=$(stat -c%s "$OUT.partial" 2>/dev/null || echo 0)
if [ "$SZ" -lt 1000000 ]; then
  echo "✗ $(date '+%F %T') дамп подозрительно мал (${SZ}B) — НЕ ротирую"; rm -f "$OUT.partial"; exit 1
fi
mv "$OUT.partial" "$OUT"
echo "${TSVER:-?}" > "$OUT.tsver"
echo "✓ $(date '+%F %T') бэкап: $OUT ($((SZ/1024/1024)) MB, ts=${TSVER:-?})"

# Ротация: оставляем KEEP самых свежих .dump (+ их .tsver).
mapfile -t OLD < <(ls -1t "$DEST"/geo-*.dump 2>/dev/null | tail -n +$((KEEP+1)) || true)
for f in "${OLD[@]:-}"; do [ -n "$f" ] && { rm -f "$f" "$f.tsver"; echo "  ротация: удалён $f"; }; done
