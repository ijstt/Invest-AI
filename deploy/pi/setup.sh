#!/usr/bin/env bash
# Установка торгового процесса (Трек 2) на Raspberry Pi. Запускать НА Pi из корня репозитория
# (после deploy/pi/sync-from-main.sh с главной машины).
#
#   cd ~/News && ./deploy/pi/setup.sh
#
# Делает: системный таймзон-чек (FORTS-сессия завязана на MSK-настенное время!), venv, числовые
# зависимости, копирует systemd-юнит (НЕ включает — сначала настройте env и прогоните preflight).
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "==== 1/5 Проверка таймзоны (КРИТично: трейдер гейтит сессию FORTS по MSK-настенному времени) ===="
TZ_NOW="$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo '?')"
echo "  Текущая таймзона Pi: ${TZ_NOW}"
if [ "$TZ_NOW" != "Europe/Moscow" ]; then
  echo "  ⚠ НЕ Europe/Moscow! Выполните:  sudo timedatectl set-timezone Europe/Moscow"
  echo "    Иначе интрадей-цикл будет открываться/флэтить в неверные часы."
fi

echo "==== 2/5 Python venv ===="
command -v python3 >/dev/null || { echo "✗ нет python3 (sudo apt install python3 python3-venv)"; exit 1; }
python3 --version
python3 -m venv .venv
.venv/bin/pip install -U pip wheel

echo "==== 3/5 Базовые зависимости пакета (pip install -e .) ===="
.venv/bin/pip install -e .

echo "==== 4/5 Числовой стек трейдера (без torch/transformers) ===="
.venv/bin/pip install -r deploy/pi/requirements-futrader.txt

echo "==== 5/5 Установка systemd-юнита (user) ===="
mkdir -p "$HOME/.config/systemd/user"
cp deploy/pi/geo-futrader.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
echo "  Юнит установлен (пока НЕ запущен)."

echo
echo "✓ Готово. Дальше:"
echo "  1) cp deploy/pi/geo-futrader.env.example deploy/pi/geo-futrader.env  и впишите GEO_DB_*"
echo "  2) ./deploy/pi/preflight.sh        # проверка связи с БД и свежести данных"
echo "  3) systemctl --user enable --now geo-futrader"
echo "  4) journalctl --user -u geo-futrader -f   # должно появиться futrader_loop_start"
echo "  (для автозапуска после ребута Pi без логина:  sudo loginctl enable-linger \$USER )"
