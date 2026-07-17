"""Adversarial and boundary test cases for the web dashboard endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from geoanalytics.api import app as api_app
from geoanalytics.api import web
from geoanalytics.query.asset_report import AssetReport
from geoanalytics.analytics.portfolio import PortfolioReport

client = TestClient(api_app.app)


def test_asset_partial_edge_cases(monkeypatch):
    # Setup standard mocks so that when a found ticker is requested, it returns a stub.
    report_sber = AssetReport(ticker="SBER", found=True, name="Сбербанк",
                             indicators={"last": 300.0, "rsi14": 55.0})
    report_notfound = AssetReport(ticker="NOTFOUND", found=False)
    
    def mock_build_report(ticker, *args, **kwargs):
        if ticker.upper() == "SBER":
            return report_sber
        return report_notfound

    monkeypatch.setattr(web, "build_report", mock_build_report)
    monkeypatch.setattr(web, "_asset_ohlcv", lambda *a, **kw: [])
    monkeypatch.setattr(web, "list_assets", lambda: [])

    # 1. Empty ticker
    r = client.get("/ui/partials/asset?ticker=")
    assert r.status_code == 200
    assert "Введите тикер" in r.text

    # 2. Whitespace-only ticker
    r = client.get("/ui/partials/asset?ticker=%20%20%20")
    assert r.status_code == 200
    assert "Введите тикер" in r.text

    # 3. Lowercase ticker (should match, because build_report maps to upper case)
    r = client.get("/ui/partials/asset?ticker=sber")
    assert r.status_code == 200
    assert "Сбербанк" in r.text

    # 4. Ticker with spaces around it
    r = client.get("/ui/partials/asset?ticker=%20sber%20")
    assert r.status_code == 200
    assert "Сбербанк" in r.text or "Актив не найден" in r.text

    # 5. Non-existent ticker
    r = client.get("/ui/partials/asset?ticker=NOTFOUND")
    assert r.status_code == 200
    assert "Актив не найден" in r.text

    # 6. Extremely long ticker string (should not crash)
    r = client.get(f"/ui/partials/asset?ticker={'A'*1000}")
    assert r.status_code == 200
    assert "Актив не найден" in r.text

    # 7. SQL injection/special characters
    r = client.get("/ui/partials/asset?ticker=SBER;%20DROP%20TABLE%20assets;%20--")
    assert r.status_code == 200
    assert "Актив не найден" in r.text


def test_asset_chart_partial_edge_cases(monkeypatch):
    monkeypatch.setattr(web, "_asset_ohlcv", lambda *a, **kw: [])
    
    # 1. Empty ticker
    r = client.get("/ui/partials/asset/chart?ticker=")
    assert r.status_code == 200
    assert "Введите тикер" in r.text

    # 2. Extreme parameter values
    # Should fallback or handle gracefully rather than crash.
    r = client.get("/ui/partials/asset/chart?ticker=SBER&range=invalid_range&period=Z&kind=unknown_kind")
    assert r.status_code == 200


def test_portfolio_actions_swallow_failures(monkeypatch):
    # Ensure that invalid/empty parameters do not result in a server crash (500)
    def mock_add_position(ticker, quantity, price):
        if quantity <= 0:
            raise ValueError("количество должно быть положительным")
        # normal add behavior
        pass

    monkeypatch.setattr(web, "_add_position", mock_add_position)
    monkeypatch.setattr(web, "_portfolio_context", lambda: {
        "report": PortfolioReport(error="портфель пуст"), "correlations": [],
        "exposure": [], "assets": []
    })

    # 1. Negative quantity (ValueError is caught and page is rendered)
    r = client.post("/ui/portfolio/add", data={"ticker": "SBER", "quantity": "-10"})
    assert r.status_code == 200
    
    # 2. Zero quantity
    r = client.post("/ui/portfolio/add", data={"ticker": "SBER", "quantity": "0"})
    assert r.status_code == 200

    # 3. Non-numeric quantity/price (FastAPI will validate and return 422, which is also correct)
    r = client.post("/ui/portfolio/add", data={"ticker": "SBER", "quantity": "abc"})
    assert r.status_code == 422


def test_track2_template_missing_fields(monkeypatch):
    """Test that the _track2.html template does not crash if fields are None or omitted."""
    from geoanalytics.api.charts import sparkline
    from geoanalytics.futrader.risk_limits import RiskLimits
    from geoanalytics.futrader.track import TrackMetrics, TrackRecord
    
    rec = TrackRecord(account="demo", starting_cash=100000.0, equity=102500.0,
                      realized_pnl=1800.0, unrealized_pnl=700.0, drawdown_pct=1.2,
                      gross_margin=48000.0, open_positions=1, metrics=TrackMetrics())
    
    # We omit unreal_pct and duration_bars completely from the positions dictionary.
    # The template should render safely using defined check.
    monkeypatch.setattr(web, "_track2_context", lambda: {
        "account": "demo", "rec": rec, "metrics": rec.metrics, "risk": None,
        "limits": RiskLimits(), "halt": None, "value_chart": None,
        "positions": [{"asset_code": "BR", "interval": "1h", "source": "rsi", "net_qty": 1,
                       "avg_price": 70.5, "last_price": 71.2, "realized_pnl": 0.0}],
        "trades": [], "drift": [], "by_strategy": [], "strat_max": 0.0,
        "by_instrument": [], "instr_max": 0.0
    })
    
    r = client.get("/ui/track2")
    assert r.status_code == 200
    assert "BR" in r.text
    assert "—" in r.text  # should render dash for missing unreal_pct/duration_bars
