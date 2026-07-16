#!/usr/bin/env bash
# Установка standalone Xray + sing-box TUN на главную машину (x86_64).
# Использует бинарники из /opt/happ/bin/ (уже установлены с Happ).
# Заменяет Happ GUI → автозапуск через systemd, полный TUN-туннель.
#
#   sudo ./deploy/vpn/vpn-setup.sh          # установить и запустить
#   sudo ./deploy/vpn/vpn-setup.sh stop     # остановить VPN
#   sudo ./deploy/vpn/vpn-setup.sh status   # проверить статус
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
XRAY_BIN="/opt/happ/bin/core/xray"
SINGBOX_BIN="/opt/happ/bin/tun/sing-box"
XRAY_CONF_DIR="/usr/local/etc/xray"
SINGBOX_CONF_DIR="/usr/local/etc/sing-box"

# --- Команды управления ---
case "${1:-install}" in
  stop)
    echo "▶ Останавливаю VPN…"
    systemctl stop sing-box-tun xray 2>/dev/null || true
    echo "✓ VPN остановлен"
    exit 0
    ;;
  start)
    echo "▶ Запускаю VPN…"
    systemctl start xray sing-box-tun
    sleep 2
    echo "✓ VPN запущен"
    _show_status() { :; }  # skip
    systemctl is-active xray sing-box-tun --no-pager
    echo "▶ Проверяю IP…"
    IP=$(curl -s --max-time 10 https://api.ipify.org 2>/dev/null || echo "не удалось определить")
    echo "  Внешний IP: $IP"
    exit 0
    ;;
  status)
    systemctl status xray sing-box-tun --no-pager 2>/dev/null || true
    echo "---"
    ip addr show | grep -A2 tun 2>/dev/null || echo "TUN-интерфейс не найден"
    echo "---"
    echo "Внешний IP: $(curl -s --max-time 10 https://api.ipify.org 2>/dev/null || echo 'не удалось определить')"
    exit 0
    ;;
  install) ;;
  *)
    echo "Использование: $0 {install|start|stop|status}" >&2
    exit 1
    ;;
esac

# --- Установка ---
echo "▶ Standalone VPN setup (Xray + sing-box TUN)"

# Проверяем бинарники
for bin in "$XRAY_BIN" "$SINGBOX_BIN"; do
  if [ ! -x "$bin" ]; then
    echo "✗ Бинарник не найден: $bin" >&2
    echo "  Установите Happ или скачайте бинарники вручную." >&2
    exit 1
  fi
done
echo "  Xray:     $($XRAY_BIN version 2>&1 | head -1)"
echo "  sing-box: $($SINGBOX_BIN version 2>&1 | head -1)"

# Останавливаем Happ, если запущен (конфликт портов)
if pgrep -x happd >/dev/null 2>&1; then
  echo "▶ Останавливаю Happ (happd) — конфликт портов с standalone…"
  # Graceful: сначала убиваем GUI, потом daemon
  pkill -x Happ 2>/dev/null || true
  sleep 1
  # happd управляет xray — остановка happd остановит и его xray
  pkill -x happd 2>/dev/null || true
  sleep 2
  # На случай если xray от Happ ещё висит
  if ss -tlnp 2>/dev/null | grep -q ":10808.*xray"; then
    pkill -f "/opt/happ/bin/core/xray" 2>/dev/null || true
    sleep 1
  fi
  echo "  Happ остановлен."
fi

# Отключаем автозапуск Happ (если есть systemd-юнит или autostart)
HAPP_AUTOSTART="$HOME/.config/autostart/Happ.desktop"
if [ -f "$HAPP_AUTOSTART" ]; then
  echo "▶ Отключаю автозапуск Happ GUI…"
  mv "$HAPP_AUTOSTART" "$HAPP_AUTOSTART.disabled"
fi
# happd может быть в system-level systemd
systemctl disable happd 2>/dev/null || true

# Ставим конфиги
echo "▶ Устанавливаю конфиги…"
mkdir -p "$XRAY_CONF_DIR" "$SINGBOX_CONF_DIR"
install -m644 "$SCRIPT_DIR/xray-config.json" "$XRAY_CONF_DIR/config.json"
install -m644 "$SCRIPT_DIR/tun-config.json"  "$SINGBOX_CONF_DIR/config.json"

# Ставим systemd-юниты
echo "▶ Устанавливаю systemd-юниты…"
install -m644 "$SCRIPT_DIR/xray.service"         /etc/systemd/system/xray.service
install -m644 "$SCRIPT_DIR/sing-box-tun.service"  /etc/systemd/system/sing-box-tun.service
systemctl daemon-reload

# Запускаем
echo "▶ Запускаю Xray…"
systemctl enable --now xray
sleep 1

# Проверяем что SOCKS работает перед стартом TUN
echo "▶ Проверяю Xray (SOCKS5 :10808)…"
if curl -s -o /dev/null -w "%{http_code}" --max-time 10 --socks5-hostname 127.0.0.1:10808 https://api.telegram.org/ 2>/dev/null | grep -q "^[23]"; then
  echo "  ✓ Xray SOCKS5 работает"
else
  echo "  ✗ Xray SOCKS5 не отвечает — проверьте логи: journalctl -u xray" >&2
  exit 1
fi

echo "▶ Запускаю sing-box TUN…"
systemctl enable --now sing-box-tun
sleep 3

# Проверяем TUN
if ip link show | grep -q tun; then
  echo "  ✓ TUN-интерфейс создан"
else
  echo "  ⚠ TUN-интерфейс не найден — проверьте логи: journalctl -u sing-box-tun"
fi

# Финальная проверка IP
echo "▶ Проверяю внешний IP…"
IP=$(curl -s --max-time 15 https://api.ipify.org 2>/dev/null || echo "не удалось определить")
echo "  Внешний IP: $IP"
echo ""

echo "✓ Установка завершена."
echo ""
echo "Управление:"
echo "  sudo $0 status   — статус"
echo "  sudo $0 stop     — остановить VPN"
echo "  sudo $0 start    — запустить VPN"
echo "  journalctl -u xray -f         — логи Xray"
echo "  journalctl -u sing-box-tun -f — логи TUN"
