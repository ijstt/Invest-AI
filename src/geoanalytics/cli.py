"""CLI на Typer — точка входа `geo`.

Команды:
  geo sources              — список доступных источников
  geo ingest [--source]    — собрать данные (один источник или все)
  geo news                 — сводка «что по новостям»
  geo asset TICKER         — аналитика по активу
  geo db upgrade           — применить миграции БД
  geo run-scheduler        — периодический сбор (заготовка)
  geo run-futrader         — Трек 2: автономный торговый цикл (отдельный процесс, Pi-ready)
"""

from __future__ import annotations

import geoanalytics.cli.backtest
import geoanalytics.cli.futrader
import geoanalytics.cli.market
import geoanalytics.cli.nlp
import geoanalytics.cli.pipeline
import geoanalytics.cli.portfolio
import geoanalytics.cli.services
from geoanalytics.cli.common import app

__all__ = ["app"]

if __name__ == "__main__":
    app()
