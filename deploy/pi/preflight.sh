#!/usr/bin/env bash
# Преполётная проверка торгового процесса на Pi: связь с БД главной машины, доступ к таблицам,
# свежесть данных, которые читает трейдер (market_regimes/market_sentiment), и чистый импорт CLI.
# Запускать НА Pi из корня репозитория ПОСЛЕ setup.sh и настройки deploy/pi/geo-futrader.env.
set -euo pipefail
cd "$(dirname "$0")/../.."

ENV_FILE="deploy/pi/geo-futrader.env"
[ -f "$ENV_FILE" ] || { echo "✗ нет $ENV_FILE — скопируйте из geo-futrader.env.example и заполните"; exit 1; }
set -a; . "$ENV_FILE"; set +a

echo "==== Импорт CLI/трейдера (ловит недостающие ARM-зависимости) ===="
PYTHONPATH="$PWD" .venv/bin/geo run-futrader --help >/dev/null && echo "  geo run-futrader импортируется ✅"
PYTHONPATH="$PWD" .venv/bin/python -c "from geoanalytics.futrader.session import in_session; import datetime; print('  FORTS-сессия сейчас:', in_session(datetime.datetime.now(), evening=True, allow_weekend=True))"

echo "==== Связь с БД и данные ===="
PYTHONPATH="$PWD" .venv/bin/python - <<'PY'
import os
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

url = URL.create("postgresql+psycopg",
                 username=os.environ["GEO_DB_USER"], password=os.environ.get("GEO_DB_PASSWORD", ""),
                 host=os.environ["GEO_DB_HOST"], port=int(os.environ.get("GEO_DB_PORT", "5432")),
                 database=os.environ["GEO_DB_NAME"])
eng = create_engine(url, connect_args={"connect_timeout": 5})
with eng.connect() as c:
    ver = c.execute(text("select version()")).scalar()
    print("  connect OK:", ver.split(",")[0])
    for t in ("futures_candles", "futures_decisions", "market_regimes", "market_sentiment"):
        try:
            n = c.execute(text(f"select count(*) from {t}")).scalar()
            print(f"  {t}: {n} строк")
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠ {t}: {e}")
    # Свежесть данных, которые читает трейдер (их пишет scheduler главной машины).
    for t in ("market_regimes", "market_sentiment"):
        try:
            d = c.execute(text(f"select max(day) from {t}")).scalar()
            print(f"  свежесть {t}.day: {d}")
        except Exception as e:  # noqa: BLE001
            print(f"  (пропуск свежести {t}: {e})")
print("  ✅ БД доступна. Можно включать службу: systemctl --user enable --now geo-futrader")
PY
