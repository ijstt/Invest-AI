#!/usr/bin/env bash
# Синхронизация кода с ГЛАВНОЙ машины на Raspberry Pi (репозиторий git не ведётся → rsync).
# Запускать НА ГЛАВНОЙ машине.
#
#   PI_HOST=pi@raspberrypi.local ./deploy/pi/sync-from-main.sh
#   ./deploy/pi/sync-from-main.sh pi@192.168.0.50          # хост можно передать аргументом
#
# Исключаем то, что НЕ переносится на ARM/Pi: x86-venv, тяжёлые данные/модели, кэши.
# Pi пересоберёт venv (deploy/pi/setup.sh) и переобучит свои joblib-модели на первом цикле.
set -euo pipefail
cd "$(dirname "$0")/../.."

PI_HOST="${1:-${PI_HOST:-}}"
PI_PATH="${PI_PATH:-News}"
if [ -z "$PI_HOST" ]; then
  echo "Использование: PI_HOST=user@pi-host $0   (или: $0 user@pi-host)" >&2
  exit 2
fi

echo "▶ rsync → ${PI_HOST}:${PI_PATH}/"
rsync -av --delete \
  --exclude '.venv/' \
  --exclude 'data/' \
  --exclude 'models/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.git/' \
  --exclude 'graphify-out/' \
  --exclude '*.log' \
  --exclude 'deploy/pi/geo-futrader.env' \
  ./ "${PI_HOST}:${PI_PATH}/"
echo "✓ Код на Pi. Дальше на Pi: deploy/pi/setup.sh → настроить env → preflight.sh → enable службу."
