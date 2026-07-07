"""Тесты REST API (M5) на TestClient. БД не нужна — query-функции замоканы."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from geoanalytics.analytics.backtest import BacktestResult, Trade
from geoanalytics.api import app as api_app
from geoanalytics.query.asset_report import AssetReport
from geoanalytics.query.news_summary import MarketSnapshot

client = TestClient(api_app.app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["sources"] >= 9  # 9 источников после M4


def test_sources():
    r = client.get("/sources")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()}
    assert {"interfax", "rbc", "fred", "ecb", "moex"} <= names


def test_news(monkeypatch):
    snap = MarketSnapshot(
        key_rate=14.5, key_rate_date="03.06.2026", fx={"USD": 78.5},
        sentiment_breakdown={"neutral": 3, "negative": 1},
        top_events=[("macro", 2)],
        headlines=[{"title": "Заголовок", "sentiment": "neutral",
                    "event_type": "macro", "url": None}],
    )
    monkeypatch.setattr(api_app, "build_snapshot", lambda **kw: snap)
    r = client.get("/news")
    assert r.status_code == 200
    body = r.json()
    assert body["key_rate"] == 14.5
    assert body["fx"]["USD"] == 78.5
    assert body["top_events"] == [["macro", 2]]  # кортеж сериализуется в массив
    assert body["headlines"][0]["title"] == "Заголовок"


def test_asset_found(monkeypatch):
    report = AssetReport(
        ticker="SBER", found=True, name="Сбербанк", sector="Финансы",
        indicators={"last": 300.0, "rsi14": 55.0},
        news=[{"title": "Сбер отчитался", "sentiment": "positive", "event_type": "earnings"}],
    )
    monkeypatch.setattr(api_app, "build_report", lambda *a, **kw: report)
    r = client.get("/asset/sber")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "SBER"
    assert body["found"] is True
    assert body["indicators"]["last"] == 300.0
    assert body["news"][0]["sentiment"] == "positive"


def test_asset_not_found(monkeypatch):
    report = AssetReport(ticker="XXXX", found=False, note="Актив не найден.")
    monkeypatch.setattr(api_app, "build_report", lambda *a, **kw: report)
    r = client.get("/asset/xxxx")
    assert r.status_code == 404
    assert "не найден" in r.json()["detail"]


def test_backtest_ok(monkeypatch):
    result = BacktestResult(
        bars=4, total_return_pct=5.0, buy_hold_return_pct=3.0,
        max_drawdown_pct=1.0, num_trades=1, exposure=0.5,
        equity_curve=[1.0, 1.05],
        trades=[Trade(entry_idx=0, exit_idx=1, entry_price=100.0, exit_price=105.0)],
    )
    monkeypatch.setattr(api_app, "backtest_asset", lambda *a, **kw: result)
    r = client.get("/backtest/sber?strategy=rsi")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "SBER"
    assert body["strategy"] == "rsi"
    assert body["total_return_pct"] == 5.0
    assert body["trades"][0]["ret_pct"] == 5.0  # вычисляемое свойство Trade


def test_backtest_unknown_strategy(monkeypatch):
    def _raise(*a, **kw):
        raise ValueError("Неизвестная стратегия: foo")
    monkeypatch.setattr(api_app, "backtest_asset", _raise)
    r = client.get("/backtest/sber?strategy=foo")
    assert r.status_code == 400
    assert "стратегия" in r.json()["detail"].lower()


def test_backtest_asset_not_found(monkeypatch):
    monkeypatch.setattr(api_app, "backtest_asset", lambda *a, **kw: None)
    r = client.get("/backtest/zzzz")
    assert r.status_code == 404


def test_events(monkeypatch):
    feed = [{
        "event_type": "sanctions", "title": "Новые санкции",
        "occurred_at": "2026-06-03T10:00:00+00:00",
        "impacts": [{"ticker": "SBER", "direction": "negative", "magnitude": 0.7}],
    }]
    monkeypatch.setattr(api_app, "recent_events", lambda **kw: feed)
    r = client.get("/events")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["event_type"] == "sanctions"
    assert body[0]["impacts"][0]["ticker"] == "SBER"


def test_alerts(monkeypatch):
    feed = [{
        "alert_type": "price_move", "ticker": "SBER", "severity": "critical",
        "title": "SBER: ▼ -6.20%", "message": "тест",
        "created_at": "2026-06-04T10:00:00+00:00", "channels": ["console", "telegram"],
    }]
    monkeypatch.setattr(api_app, "recent_alerts", lambda **kw: feed)
    r = client.get("/alerts")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["alert_type"] == "price_move"
    assert body[0]["channels"] == ["console", "telegram"]


@pytest.mark.parametrize("path", ["/health", "/sources"])
def test_no_db_routes_ok(path):
    """Роуты без БД отвечают без моков."""
    assert client.get(path).status_code == 200
