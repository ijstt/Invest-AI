"""Трек 2 / Фаза 0: тесты накопления пулинг-датасета — оркестрация, глубина окна, изоляция сбоев."""

from __future__ import annotations

import pytest

import geoanalytics.futrader.accumulate as acc
from geoanalytics.futrader.accumulate import AccumResult, accumulate_dataset
from geoanalytics.futrader.decisions import LogResult


@pytest.fixture(autouse=True)
def _stub_edge(monkeypatch):
    """EdgeContext грузит Трек-1 из БД — в этих оркестрационных тестах заглушаем (session=None).

    Кросс-секционный логгер (Пул 9/E) тоже требует БД/сети — нейтрализуем (XSEC-строка/интервал)."""
    monkeypatch.setattr(acc, "EdgeContext", lambda session, **k: object())
    monkeypatch.setattr(acc, "log_cross_sectional_decisions", lambda *a, **k: LogResult())


def test_aggregates_across_instruments_and_strategies(monkeypatch):
    log_calls = []

    def fake_backfill(session, ticker, *, interval, days, max_contracts):
        return 10  # новых свечей

    def fake_log(session, ticker, interval, *, source, horizon_bars, edge=None):
        log_calls.append((ticker, interval, source))
        return LogResult(stored=2, labeled=1, wins=1)

    monkeypatch.setattr(acc, "backfill_futures_intraday", fake_backfill)
    monkeypatch.setattr(acc, "log_decisions", fake_log)

    res = accumulate_dataset(None, tickers=("BR", "GD"), intervals=("1h",),
                             strategies=("sma_cross", "rsi"))
    assert isinstance(res, AccumResult)
    assert len(res.stats) == 3          # 2 инструмента × 1 интервал + 1 XSEC-строка
    assert res.candles == 20            # 2 × 10
    assert res.decisions == 8           # 2 инстр × 2 страт × stored 2 (XSEC застаблен 0)
    assert res.labeled == 4             # 2 инстр × 2 страт × labeled 1
    assert len(log_calls) == 4


def test_days_picked_per_interval(monkeypatch):
    seen = []

    def fake_backfill(session, ticker, *, interval, days, max_contracts):
        seen.append((interval, days))
        return 0

    monkeypatch.setattr(acc, "backfill_futures_intraday", fake_backfill)
    monkeypatch.setattr(acc, "log_decisions", lambda *a, **k: LogResult())

    accumulate_dataset(None, tickers=("BR",), intervals=("1h", "1d"), strategies=("rsi",))
    assert dict(seen) == {"1h": acc.INTERVAL_DAYS["1h"], "1d": acc.INTERVAL_DAYS["1d"]}


def test_days_override_applies_to_all(monkeypatch):
    seen = []

    def fake_backfill(session, ticker, *, interval, days, max_contracts):
        seen.append(days)
        return 0

    monkeypatch.setattr(acc, "backfill_futures_intraday", fake_backfill)
    monkeypatch.setattr(acc, "log_decisions", lambda *a, **k: LogResult())

    accumulate_dataset(None, tickers=("BR",), intervals=("1h", "1d"), strategies=("rsi",), days=30)
    assert seen == [30, 30]


def test_backfill_failure_isolated(monkeypatch):
    log_calls = []

    def fake_backfill(session, ticker, *, interval, days, max_contracts):
        if ticker == "BR":
            raise RuntimeError("ISS down")
        return 5

    def fake_log(session, ticker, interval, *, source, horizon_bars, edge=None):
        log_calls.append(ticker)
        return LogResult(stored=1, labeled=1, wins=0)

    monkeypatch.setattr(acc, "backfill_futures_intraday", fake_backfill)
    monkeypatch.setattr(acc, "log_decisions", fake_log)

    res = accumulate_dataset(None, tickers=("BR", "GD"), intervals=("1h",), strategies=("rsi",))
    assert len(res.stats) == 3          # обе инстр-строки + 1 XSEC-строка
    assert res.candles == 5             # только GD
    assert log_calls == ["GD"]          # стратегии упавшего BR не логировались


def test_strategy_failure_isolated(monkeypatch):
    def fake_backfill(session, ticker, *, interval, days, max_contracts):
        return 3

    def fake_log(session, ticker, interval, *, source, horizon_bars, edge=None):
        if source == "macd":
            raise RuntimeError("bad series")
        return LogResult(stored=2, labeled=2, wins=1)

    monkeypatch.setattr(acc, "backfill_futures_intraday", fake_backfill)
    monkeypatch.setattr(acc, "log_decisions", fake_log)

    res = accumulate_dataset(None, tickers=("BR",), intervals=("1h",),
                             strategies=("macd", "rsi"))
    # macd упал, rsi прошёл — частичный прогресс сохранён.
    assert res.decisions == 2
    assert res.labeled == 2


def test_unknown_interval_skipped(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("не должно вызваться")

    monkeypatch.setattr(acc, "backfill_futures_intraday", boom)
    monkeypatch.setattr(acc, "log_decisions", lambda *a, **k: LogResult())
    res = accumulate_dataset(None, tickers=("BR",), intervals=("5s",), strategies=("rsi",))
    assert res.stats == []
