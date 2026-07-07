#!/usr/bin/env bash
# Поднять всю инфраструктуру одной командой:
#   ./up.sh              # docker (БД+Ollama) → миграции → сид → перезапуск служб
#   ./up.sh --backfill   # дополнительно догрузить историю котировок с MOEX (долго)
#   ./up.sh --relink     # дополнительно перелинковать новости (significance/связи, после M6)
#   ./up.sh --no-restart # не трогать systemd-службы (geo-alerts/geo-dashboard)
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="$PWD"

DB_USER="${GEO_DB_USER:-geo}"
DB_NAME="${GEO_DB_NAME:-geoanalytics}"
DO_BACKFILL=0 DO_RELINK=0 DO_RESTART=1
for arg in "$@"; do
  case "$arg" in
    --backfill) DO_BACKFILL=1 ;;
    --relink) DO_RELINK=1 ;;
    --no-restart) DO_RESTART=0 ;;
    *) echo "Неизвестный флаг: $arg" >&2; exit 2 ;;
  esac
done

echo "▶ Поднимаю контейнеры (geo-db, geo-ollama)…"
docker compose up -d

echo "▶ Жду готовности БД…"
for _ in $(seq 1 30); do
  if docker exec geo-db pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
    READY=1; break
  fi
  sleep 2
done
[[ "${READY:-}" == 1 ]] || { echo "✗ БД не поднялась за 60с — смотри 'docker logs geo-db'"; exit 1; }
echo "  БД готова."

# Модели Ollama: создаём из Modelfile по разу, если их ещё нет (на CPU — небыстро).
# 7B — синтез отчётов (GEO_LLM_MODEL), 3B — лёгкий ask-роутер (GEO_LLM_ROUTER_MODEL).
ensure_model() {  # $1 тег, $2 Modelfile в /import
  if ! docker exec geo-ollama ollama list 2>/dev/null | grep -q "$1"; then
    echo "▶ Модель Ollama '$1' не найдена — создаю из $2 (разово)…"
    docker exec geo-ollama ollama create "$1" -f "$2" \
      || echo "  ⚠ не удалось создать '$1' — соответствующий путь LLM недоступен (не критично)"
  fi
}
ensure_model qwen2.5:7b-instruct /import/Modelfile
ensure_model qwen2.5:3b-instruct /import/Modelfile-3b

echo "▶ Миграции БД…"
PYTHONPATH="$PWD" .venv/bin/geo db upgrade
echo "▶ Справочник эмитентов (идемпотентно)…"
PYTHONPATH="$PWD" .venv/bin/geo db seed

if [[ "$DO_BACKFILL" == 1 ]]; then
  echo "▶ Догружаю историю котировок с MOEX (это долго)…"
  PYTHONPATH="$PWD" .venv/bin/geo backfill
fi
if [[ "$DO_RELINK" == 1 ]]; then
  echo "▶ Перелинковка новостей (significance + связи)…"
  PYTHONPATH="$PWD" .venv/bin/geo relink
fi

if [[ "$DO_RESTART" == 1 ]]; then
  if systemctl --user list-unit-files 2>/dev/null | grep -q "geo-dashboard"; then
    echo "▶ Перезапускаю службы geo-alerts, geo-dashboard…"
    systemctl --user restart geo-alerts geo-dashboard
    echo "  Дашборд: http://127.0.0.1:8800/"
  else
    echo "ℹ systemd-службы не найдены. Дашборд вручную:  geo serve"
  fi
fi

echo "✓ Готово."
