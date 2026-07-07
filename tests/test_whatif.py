"""Тесты J4: сценарный анализ «что-если»."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from geoanalytics.analytics.whatif import (
    portfolio_scenario,
    scenario_move,
    whatif_portfolio,
)


# --------------------------------------------------------------------------- #
# Чистое ядро
# --------------------------------------------------------------------------- #
def test_scenario_move_weighted_sum():
    """move = Σ β_f·shock_f с поимёнными вкладами."""
    move, contrib, missing = scenario_move(
        {"market": 1.2, "usd_rub": -0.5}, {"market": -5.0, "usd_rub": 10.0}
    )
    assert move == -11.0   # 1.2·(−5) + (−0.5)·10
    assert contrib == {"market": -6.0, "usd_rub": -5.0}
    assert missing == []


def test_scenario_move_missing_factor():
    """Шок по фактору без беты → missing, на move не влияет."""
    move, contrib, missing = scenario_move(
        {"market": 1.0}, {"market": -5.0, "brent": -10.0}
    )
    assert move == -5.0
    assert "brent" in missing
    assert "brent" not in contrib


def test_scenario_move_zero_shocks():
    move, contrib, _ = scenario_move({"market": 1.5}, {"market": 0.0})
    assert move == 0.0
    assert contrib == {"market": 0.0}


def test_portfolio_scenario_weighted():
    """Движение портфеля = Σ вес·движение актива."""
    move = portfolio_scenario(
        {"AAA": 0.7, "BBB": 0.3}, {"AAA": -10.0, "BBB": 5.0}
    )
    assert move == -5.5


# --------------------------------------------------------------------------- #
# DB-раннер (моки)
# --------------------------------------------------------------------------- #
def _attr(ticker: str, betas: dict[str, float], r2: float = 0.4):
    return SimpleNamespace(ticker=ticker, error=None, betas=betas, r2=r2, n_obs=200)


def test_whatif_portfolio_full():
    """Портфель из двух активов: движение, P&L в ₽, caveat про R²."""
    ds = [date(2026, 1, 1) + timedelta(days=i) for i in range(5)]
    asset_a = SimpleNamespace(id=1, ticker="AAA")
    asset_b = SimpleNamespace(id=2, ticker="BBB")
    pos = SimpleNamespace(quantity=10.0)
    repo = MagicMock()
    repo.list_positions.return_value = [(asset_a, pos), (asset_b, pos)]
    # AAA: последняя цена 100 → 1000₽; BBB: 300 → 3000₽. Веса 0.25/0.75.
    levels = {1: {d: 100.0 for d in ds}, 2: {d: 300.0 for d in ds}}
    attrs = {"AAA": _attr("AAA", {"market": 2.0}),
             "BBB": _attr("BBB", {"market": 1.0, "brent": 0.5})}

    with (
        patch("geoanalytics.analytics.whatif.PortfolioRepository",
              return_value=repo),
        patch("geoanalytics.analytics.whatif._price_levels",
              side_effect=lambda _s, aid: levels[aid]),
        patch("geoanalytics.analytics.whatif._asset_returns",
              return_value={}),
        patch("geoanalytics.analytics.whatif.attribute_asset",
              side_effect=lambda _s, t, **kw: attrs[t.upper()]),
    ):
        r = whatif_portfolio(MagicMock(), {"market": -5.0, "brent": -10.0})

    assert r.error is None
    moves = {a.ticker: a.expected_move_pct for a in r.assets}
    assert moves["AAA"] == -10.0          # 2·(−5), brent — missing
    assert moves["BBB"] == -10.0          # 1·(−5) + 0.5·(−10)
    assert r.portfolio_move_pct == -10.0  # 0.25·(−10) + 0.75·(−10)
    assert r.total_value_rub == 4000.0
    assert r.portfolio_pnl_rub == -400.0
    assert any("R²" in c for c in r.caveats)
    aaa = next(a for a in r.assets if a.ticker == "AAA")
    assert "brent" in aaa.missing_factors


def test_whatif_empty_portfolio():
    repo = MagicMock()
    repo.list_positions.return_value = []
    with patch("geoanalytics.analytics.whatif.PortfolioRepository",
               return_value=repo):
        r = whatif_portfolio(MagicMock(), {"market": -5.0})
    assert r.error is not None and "пуст" in r.error


def test_whatif_skipped_assets_in_caveats():
    """Актив без бет пропускается и честно попадает в оговорки."""
    ds = [date(2026, 1, 1) + timedelta(days=i) for i in range(3)]
    asset_a = SimpleNamespace(id=1, ticker="AAA")
    asset_b = SimpleNamespace(id=2, ticker="NEW")
    pos = SimpleNamespace(quantity=1.0)
    repo = MagicMock()
    repo.list_positions.return_value = [(asset_a, pos), (asset_b, pos)]
    levels = {1: {d: 100.0 for d in ds}, 2: {d: 50.0 for d in ds}}

    def fake_attr(_s, t, **kw):
        if t.upper() == "NEW":
            return SimpleNamespace(error="мало данных для регрессии")
        return _attr("AAA", {"market": 1.0})

    with (
        patch("geoanalytics.analytics.whatif.PortfolioRepository",
              return_value=repo),
        patch("geoanalytics.analytics.whatif._price_levels",
              side_effect=lambda _s, aid: levels[aid]),
        patch("geoanalytics.analytics.whatif._asset_returns",
              return_value={}),
        patch("geoanalytics.analytics.whatif.attribute_asset",
              side_effect=fake_attr),
    ):
        r = whatif_portfolio(MagicMock(), {"market": -4.0})

    assert r.error is None
    assert [a.ticker for a in r.assets] == ["AAA"]
    assert any("NEW" in c for c in r.caveats)
