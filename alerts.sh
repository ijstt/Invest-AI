#!/usr/bin/env bash
# Простой запуск проверки алертов. Использование:
#   ./alerts.sh                  # окно по умолчанию (24ч)
#   ./alerts.sh --window 2       # окно 2 часа (меньше уведомлений — удобно для теста)
#   ./alerts.sh --no-dispatch    # только посчитать, без отправки в Telegram
set -euo pipefail
cd "$(dirname "$0")"

WINDOW=""
PASS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --window) WINDOW="$2"; shift 2 ;;
    *) PASS+=("$1"); shift ;;
  esac
done

[[ -n "$WINDOW" ]] && export GEO_ALERT_WINDOW_HOURS="$WINDOW"
PYTHONPATH=. .venv/bin/geo alerts "${PASS[@]}"
