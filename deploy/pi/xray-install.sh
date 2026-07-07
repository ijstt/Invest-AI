#!/usr/bin/env bash
# Установка Xray-core (arm64) на Raspberry Pi для split-tunnel SOCKS только под Telegram.
# Качает официальный бинарь Xray-core с github.com/XTLS/Xray-core, ставит конфиг (deploy/pi/
# xray-config.json: VLESS-Reality-xHTTP outbound + локальный SOCKS 127.0.0.1:10808) и systemd-юнит.
# Запускать НА Pi:  cd ~/News && ./deploy/pi/xray-install.sh
set -euo pipefail
cd "$(dirname "$0")/../.."

# Версия Xray. Сервер (Reality) пинит Min/Max Client Ver = 25.9.11 → ставим РОВНО её, иначе
# Reality отдаёт реальный сертификат («received real certificate»). Передать другую: ./xray-install.sh vX.Y.Z
VER="${1:-v25.9.11}"
if [ "$VER" = "latest" ]; then URL="https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-arm64-v8a.zip"
else URL="https://github.com/XTLS/Xray-core/releases/download/${VER}/Xray-linux-arm64-v8a.zip"; fi

command -v unzip >/dev/null || sudo apt-get install -y unzip

echo "▶ качаю Xray-core ${VER} (arm64)…"
curl -fL -o /tmp/xray.zip "$URL"
sudo mkdir -p /usr/local/etc/xray /usr/local/share/xray
( cd /tmp && unzip -o xray.zip xray geoip.dat geosite.dat )

echo "▶ ставлю бинарь/данные/конфиг/сервис…"
sudo install -m755 /tmp/xray /usr/local/bin/xray
sudo install -m644 /tmp/geoip.dat /tmp/geosite.dat /usr/local/share/xray/ 2>/dev/null || true
sudo install -m644 deploy/pi/xray-config.json /usr/local/etc/xray/config.json
sudo install -m644 deploy/pi/xray.service /etc/systemd/system/xray.service
sudo systemctl daemon-reload
sudo systemctl enable xray
sudo systemctl restart xray   # перечитать новый бинарь/конфиг (enable --now не рестартит работающий)
sleep 2

echo "▶ версия и статус:"
/usr/local/bin/xray version | head -1
echo -n "xray active: "; systemctl is-active xray
echo "▶ тест SOCKS → Telegram (ожидаем НЕ timeout):"
curl -s -o /dev/null -w "  telegram via proxy: HTTP %{http_code}, %{time_total}s\n" --max-time 20 --socks5-hostname 127.0.0.1:10808 https://api.telegram.org/ || echo "  (через прокси не прошло)"
