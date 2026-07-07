#!/usr/bin/env bash
# Единая «кнопка» управления всей системой geoanalytics.
#
#   ./geo-ctl.sh up        # поднять контейнеры (БД+Ollama) и службы (алерты+дашборд+бот)
#   ./geo-ctl.sh down      # остановить службы и контейнеры (данные в volume сохраняются)
#   ./geo-ctl.sh restart   # down → up
#   ./geo-ctl.sh status    # что сейчас работает (контейнеры, службы, /health)
#   ./geo-ctl.sh logs      # живой хвост логов планировщика алертов
#
# Первичная установка (миграции/сид/история) — отдельный «тяжёлый» bootstrap ./up.sh.
set -euo pipefail
cd "$(dirname "$0")"

DB_USER="${GEO_DB_USER:-geo}"
DB_NAME="${GEO_DB_NAME:-geoanalytics}"
PORT="${GEO_DASHBOARD_PORT:-8800}"
# Фаза 1: БД и трейдер/дашборд/стакан переехали на Raspberry Pi (всегда-онлайн, deploy/pi/). На
# НОУТЕ остаются только тяжёлые вычисления: scheduler (ингест+NLP) и бот (/ask через Qwen) +
# контейнер Ollama. БД — на Pi (GEO_DB_HOST). Переопределить набор служб: GEO_SERVICES="…".
DB_HOST="${GEO_DB_HOST:-192.168.0.114}"
DB_PORT="${GEO_DB_PORT:-5432}"
read -ra SERVICES <<< "${GEO_SERVICES:-geo-alerts geo-bot}"

have_services() { systemctl --user list-unit-files 2>/dev/null | grep -q "geo-alerts"; }

wait_db() {
  echo "▶ Жду готовности БД на Pi (${DB_HOST}:${DB_PORT})…"
  for _ in $(seq 1 30); do
    if (exec 3<>"/dev/tcp/${DB_HOST}/${DB_PORT}") 2>/dev/null; then
      echo "  БД доступна."; return 0
    fi
    sleep 2
  done
  echo "✗ БД на Pi недоступна за 60с — проверь, что Pi и контейнер geo-db подняты"; return 1
}

cmd_up() {
  echo "▶ Контейнер Ollama (БД — на Pi)…"
  docker compose up -d ollama
  wait_db
  if have_services; then
    echo "▶ Службы (${SERVICES[*]})…"
    systemctl --user start "${SERVICES[@]}"
    echo "  Дашборд: http://127.0.0.1:${PORT}/"
  else
    echo "ℹ systemd-службы не найдены — дашборд вручную: .venv/bin/geo serve"
  fi
  echo "✓ Поднято."
}

cmd_down() {
  if have_services; then
    echo "▶ Останавливаю службы…"
    systemctl --user stop "${SERVICES[@]}" 2>/dev/null || true
  fi
  echo "▶ Останавливаю контейнер Ollama (БД на Pi не трогаем)…"
  docker compose stop ollama
  echo "✓ Остановлено."
}

# Проверка, что службы реально подняты СВЕЖИМИ процессами (новый код с диска).
# Зачем: stop мог молча провалиться (ошибка глоталась), start по работающему юниту —
# no-op, и «restart» оставлял в памяти старый код, рапортуя успех (поймано 2026-06-12).
verify_fresh() {
  local s started age
  for s in "${SERVICES[@]}"; do
    started=$(systemctl --user show "$s" -p ExecMainStartTimestamp --value 2>/dev/null)
    # «Fri 2026-06-12 01:48:29 MSK» → берём дату и время, парсим как локальные.
    age=$(( $(date +%s) - $(date -d "$(echo "$started" | awk '{print $2" "$3}')" +%s 2>/dev/null || echo 0) ))
    if [ -z "$started" ] || [ "$age" -gt 60 ]; then
      echo "✗ $s НЕ перезапустился (процесс от: ${started:-?}). Вручную: systemctl --user restart $s" >&2
      exit 1
    fi
  done
  echo "  Службы перезапущены свежими процессами."
}

cmd_restart() {
  echo "▶ Перезапускаю контейнер Ollama (БД на Pi не трогаем)…"
  docker compose restart ollama
  wait_db
  if have_services; then
    echo "▶ Перезапускаю службы (${SERVICES[*]})…"
    systemctl --user restart "${SERVICES[@]}"
    verify_fresh
    echo "  Дашборд (на Pi): http://${DB_HOST}:${PORT}/"
  else
    echo "ℹ systemd-службы не найдены — дашборд вручную: .venv/bin/geo serve"
  fi
  echo "✓ Перезапущено."
}

cmd_status() {
  echo "=== Контейнеры ==="
  docker compose ps 2>/dev/null || docker ps --filter name=geo- --format '{{.Names}}\t{{.Status}}'
  echo "=== Службы ==="
  if have_services; then
    for s in "${SERVICES[@]}"; do printf '  %-15s %s\n' "$s" "$(systemctl --user is-active "$s" 2>/dev/null)"; done
  else
    echo "  (systemd-службы не установлены)"
  fi
  echo "=== Дашборд /health (на Pi) ==="
  curl -s --max-time 3 "http://${DB_HOST}:${PORT}/health" || echo "  (недоступен)"
  echo "=== Pi-службы (futrader/depth/dashboard) ==="
  ssh -o BatchMode=yes -o ConnectTimeout=4 "pi@${DB_HOST}" 'export XDG_RUNTIME_DIR=/run/user/$(id -u); for s in geo-futrader geo-depth geo-dashboard; do printf "  %-15s %s\n" "$s" "$(systemctl --user is-active $s)"; done' 2>/dev/null | grep -vE 'rptl.io|valid user' || echo "  (Pi недоступна по ssh)"
  echo
}

cmd_logs() { journalctl --user -u geo-alerts -f; }

case "${1:-}" in
  up) cmd_up ;;
  down) cmd_down ;;
  restart) cmd_restart ;;
  status) cmd_status ;;
  logs) cmd_logs ;;
  *) echo "Использование: $0 {up|down|restart|status|logs}" >&2; exit 2 ;;
esac
