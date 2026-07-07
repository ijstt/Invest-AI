#!/usr/bin/env bash
# Обновление трейдера на Pi С ГЛАВНОЙ машины: синк кода → (опц.) зависимости → рестарт службы.
# Запускать НА ГЛАВНОЙ машине после правок кода.
#
#   PI_HOST=pi@192.168.0.114 ./deploy/pi/update.sh            # код + рестарт
#   PI_HOST=pi@192.168.0.114 ./deploy/pi/update.sh --deps     # + переустановить зависимости
#
# ВНИМАНИЕ про схему БД: если правка добавила alembic-миграцию, примените её к БД ОТДЕЛЬНО
# (`geo db upgrade` против той БД, что использует трейдер) — этот скрипт миграции не накатывает.
set -euo pipefail
cd "$(dirname "$0")/../.."

PI_HOST="${PI_HOST:-}"
[ -n "$PI_HOST" ] || { echo "Использование: PI_HOST=user@host $0 [--deps]" >&2; exit 2; }
PI_PATH="${PI_PATH:-News}"
DEPS=0; for a in "$@"; do [ "$a" = "--deps" ] && DEPS=1; done
RT='export XDG_RUNTIME_DIR=/run/user/$(id -u);'

echo "▶ 1/3 синк кода → ${PI_HOST}:${PI_PATH}/"
PI_HOST="$PI_HOST" PI_PATH="$PI_PATH" ./deploy/pi/sync-from-main.sh "$PI_HOST" >/dev/null

if [ "$DEPS" = 1 ]; then
  echo "▶ 2/3 зависимости (переустановка)"
  ssh "$PI_HOST" "$RT cd $PI_PATH && .venv/bin/pip install -q -e . && .venv/bin/pip install -q -r deploy/pi/requirements-futrader.txt"
else
  echo "▶ 2/3 зависимости — пропуск (запустите с --deps при изменении requirements)"
fi

echo "▶ 3/3 рестарт службы"
ssh "$PI_HOST" "$RT systemctl --user restart geo-futrader && sleep 3 && printf 'geo-futrader: ' && systemctl --user is-active geo-futrader"
echo "✓ Трейдер на Pi обновлён."
