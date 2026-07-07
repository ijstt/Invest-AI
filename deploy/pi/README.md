# Перенос торгового процесса (Трек 2) на Raspberry Pi

Трейдер (`geo run-futrader`) — чисто numeric (sklearn/индикаторы), без GPU/LLM. Он читает
`market_regimes`/`market_sentiment` из Postgres **главной машины** по локальной сети и пишет в
`futures_*` таблицы там же. Инвест-каскад (трансформеры + Ollama) остаётся на главной машине.

**Главный инвариант — ОДИН писатель `futures_*`.** Пока трейдер крутится на главной машине (старый
монолитный scheduler), Pi запускать НЕЛЬЗЯ. Сначала редеплой scheduler на главной (он перестаёт
торговать), потом старт на Pi.

Данные для подстановки: **LAN-IP главной машины = `192.168.0.196`** (перепроверьте `hostname -I`).

---

## Чек-лист переноса (порядок важен)

### 0. Главная машина — открыть БД для Pi (один раз)
Контейнер `geo-db` уже публикует порт на все интерфейсы (`5432:5432` в docker-compose.yml).
Проверьте, что Pi в той же подсети (`192.168.0.x`) и порт доступен:
```bash
# с Pi:
nc -vz 192.168.0.196 5432         # должно быть "succeeded"
```
Если «refused/timeout»:
- Хост-фаервол: `sudo ufw allow from 192.168.0.0/24 to any port 5432` (если ufw активен).
- `pg_hba.conf` контейнера должен пускать подсеть. Проверка/добавление:
  ```bash
  docker exec geo-db sh -c "grep -n 0.0.0.0 \$PGDATA/pg_hba.conf"   # есть ли host all all 0.0.0.0/0
  # если нет — добавить scram-строку для LAN и перечитать конфиг:
  docker exec geo-db sh -c "echo 'host all all 192.168.0.0/24 scram-sha-256' >> \$PGDATA/pg_hba.conf"
  docker exec geo-db psql -U geo -d geoanalytics -c "SELECT pg_reload_conf();"
  ```
  (образ timescaledb-ha обычно уже содержит `host all all all scram-sha-256` — тогда ничего не нужно.)

### 1. Главная машина — редеплой scheduler БЕЗ трейдера (КРИТично до старта Pi)
Новый код уже на диске (Трек A): scheduler больше не запускает futrader-петлю.
```bash
cd ~/News
./geo-ctl.sh up                      # если стек опущен — поднять контейнеры+службы
systemctl --user restart geo-alerts  # перезапустить scheduler под новый код
journalctl --user -u geo-alerts -n 30 --no-pager   # убедиться, что futrader_* больше НЕ в логах
```
На главной машине трейдер НЕ запускается: его нет в `geo-ctl.sh` SERVICES, юнит главной машины удалён.

### 2. Главная машина — синхронизировать код на Pi
```bash
PI_HOST=pi@<имя-или-ip-Pi> ./deploy/pi/sync-from-main.sh
```
(переносит код; исключает x86-venv, data/, models/ — Pi пересоберёт.)

### 3. Pi — таймзона, venv, зависимости
```bash
sudo timedatectl set-timezone Europe/Moscow    # КРИТично: сессия FORTS гейтится по MSK
cd ~/News
./deploy/pi/setup.sh                            # venv + pip install -e . + числовой стек + юнит
```

### 4. Pi — окружение
```bash
cp deploy/pi/geo-futrader.env.example deploy/pi/geo-futrader.env
nano deploy/pi/geo-futrader.env                 # GEO_DB_HOST=192.168.0.196, GEO_DB_PASSWORD=...
```

### 5. Pi — преполёт
```bash
./deploy/pi/preflight.sh
# Ожидаем: "geo run-futrader импортируется ✅", connect OK, ненулевые futures_candles/
# market_regimes/market_sentiment и их свежесть (day = последние дни).
```

### 6. Pi — запуск
```bash
systemctl --user enable --now geo-futrader
sudo loginctl enable-linger $USER               # автозапуск после ребута Pi без логина
journalctl --user -u geo-futrader -f            # ждём futrader_loop_start, затем futrader_intraday/loop
```

---

## Проверка успеха
- На главной машине в БД: новые снимки `futures_paper_equity` и записи `futures_decisions` идут с Pi
  (по времени совпадают с циклами Pi), а в логе `geo-alerts` НЕТ строк `futrader_*`.
- Память scheduler на главной: пик ~4.5G (без бывшего ~8G-спайка) —
  `systemctl --user status geo-alerts`.
- Первый ДНЕВНОЙ цикл на Pi переобучит политики локально (`data/futrader/policy_*.joblib`); до этого
  paper может не торговать (чемпион ещё не собран на Pi) — это нормально, не ошибка.

## Откат
```bash
# на Pi — остановить трейдер:
systemctl --user disable --now geo-futrader
```
Если нужно временно вернуть трейдер на главную машину: скопируйте `deploy/pi/geo-futrader.service`
в `~/.config/systemd/user/` главной (юнит host-agnostic через `%h`), создайте там
`deploy/pi/geo-futrader.env` с `GEO_DB_HOST=127.0.0.1`, `daemon-reload`, `enable --now`. **Помните
про инвариант одного писателя — НЕ держите трейдер на Pi и на главной одновременно.**

## Траблшутинг
- **`connection refused/timeout`** → шаг 0 (фаервол/pg_hba/подсеть).
- **`password authentication failed`** → `GEO_DB_PASSWORD` в env ≠ паролю контейнера (по умолчанию `geo`).
- **`No matching distribution`/нет колеса при pip** на Pi → ослабьте пин в
  `deploy/pi/requirements-futrader.txt` (`==` → `>=`); трейдер не привязан к точной версии, кроме
  желательной согласованности sklearn для своих же joblib-моделей (Pi их пересоберёт).
- **Трейдер торгует в неверные часы / молчит в сессии** → таймзона Pi не `Europe/Moscow` (шаг 3).
- **`ModuleNotFoundError: config`** → служба должна иметь `PYTHONPATH=%h/News` (есть в юните);
  при ручном запуске — `PYTHONPATH=$PWD .venv/bin/geo run-futrader`.
