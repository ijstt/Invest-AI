"""Тесты J1: виртуальный портфель (чистое ядро + DB-раннер с моками)."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from geoanalytics.analytics.portfolio import (
    _cost_basis_rub,
    _equity_from_returns,
    aggregate_exposure,
    correlation_matrix,
    historical_var,
    portfolio_report,
    portfolio_returns,
    settled_day,
)


def _dates(n: int, start: date = date(2026, 1, 1)) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


# --------------------------------------------------------------------------- #
# historical_var
# --------------------------------------------------------------------------- #
def test_var_known_quantile():
    """VaR95 на 100 точках = 5-й перцентиль потерь."""
    returns = [i / 1000 for i in range(-50, 50)]  # −5.0%..+4.9% равномерно
    var = historical_var(returns, 0.95)
    assert var is not None
    assert 0.044 < var < 0.046  # ~4.5% потерь на 5-м перцентиле


def test_var_short_series_none():
    """Меньше min_points — None, а не шумный квантиль."""
    assert historical_var([0.01] * 5) is None
    assert historical_var([]) is None


# --------------------------------------------------------------------------- #
# portfolio_returns
# --------------------------------------------------------------------------- #
def test_portfolio_returns_weighted_sum():
    """Полное пересечение дат → взвешенная сумма доходностей."""
    ds = _dates(30)
    rets = {
        "AAA": {d: 0.01 for d in ds},
        "BBB": {d: -0.01 for d in ds},
    }
    out, excluded = portfolio_returns({"AAA": 0.75, "BBB": 0.25}, rets)
    assert excluded == []
    assert len(out) == 30
    for v in out.values():
        assert abs(v - 0.005) < 1e-12  # 0.75·1% − 0.25·1%


def test_portfolio_returns_excludes_short_history():
    """Актив с коротким покрытием исключается, веса ренормализуются."""
    ds = _dates(40)
    rets = {
        "AAA": {d: 0.01 for d in ds},
        "BBB": {d: 0.03 for d in ds},
        "NEW": {d: 0.10 for d in ds[-5:]},  # 5 общих дат < min_points
    }
    out, excluded = portfolio_returns({"AAA": 0.5, "BBB": 0.3, "NEW": 0.2}, rets)
    assert excluded == ["NEW"]
    # Ренормализация: 0.5/0.8·1% + 0.3/0.8·3% = 1.75%
    for v in out.values():
        assert abs(v - 0.0175) < 1e-12


def test_portfolio_returns_all_short():
    """Совсем нет общей истории → пустая серия, все исключены."""
    out, excluded = portfolio_returns(
        {"AAA": 1.0}, {"AAA": {d: 0.01 for d in _dates(3)}}
    )
    assert out == {}
    assert excluded == ["AAA"]


# --------------------------------------------------------------------------- #
# correlation_matrix / aggregate_exposure / equity
# --------------------------------------------------------------------------- #
def test_correlation_matrix_signs():
    """Идеальная кор/антикорреляция на синтетике → ±1.0, верхний треугольник."""
    ds = _dates(30)
    base = {d: (i % 5 - 2) / 100 for i, d in enumerate(ds)}
    rets = {
        "AAA": base,
        "BBB": {d: v * 2 for d, v in base.items()},     # ρ = +1
        "CCC": {d: -v for d, v in base.items()},        # ρ = −1
    }
    m = correlation_matrix(rets)
    assert m[("AAA", "BBB")] == 1.0
    assert m[("AAA", "CCC")] == -1.0
    assert ("BBB", "AAA") not in m  # только верхний треугольник


def test_aggregate_exposure_weighted_and_missing():
    """Σ вес·β по факторам; отсутствующая бета = 0."""
    weights = {"AAA": 0.6, "BBB": 0.4}
    betas = {
        "AAA": {"market": 1.0, "brent": 0.5},
        "BBB": {"market": 2.0},  # brent не оценён
    }
    expo = aggregate_exposure(weights, betas)
    assert expo["market"] == 1.4   # 0.6·1 + 0.4·2
    assert expo["brent"] == 0.3    # 0.6·0.5 + 0


def test_settled_day_skips_unsettled_intraday():
    """Атрибуция считается на последнем дне, покрытом и активом, и рынком."""
    ds = _dates(5)
    asset_rets = {d: 0.01 for d in ds}            # есть «сегодня» (интрадей)
    market_rets = {d: 0.0 for d in ds[:-1]}        # индекс закрылся вчера
    assert settled_day(asset_rets, market_rets) == ds[-2]
    assert settled_day(asset_rets, {}) is None


def test_equity_drawdown_known():
    """Известная просадка: −20% после пика."""
    from geoanalytics.analytics.backtest import _max_drawdown

    equity = _equity_from_returns([0.10, -0.20, 0.05])
    assert abs(_max_drawdown(equity) - 20.0) < 1e-9


# --------------------------------------------------------------------------- #
# DB-раннер (моки)
# --------------------------------------------------------------------------- #
def _mock_repo(rows):
    repo = MagicMock()
    repo.list_positions.return_value = rows
    return repo


def test_cost_basis_rub_equity_plus_cash():
    """База = Σ avg·qty по акциям + ₽-стоимость кэша (кэш и в стоимости, и в базе → 0 в P&L)."""
    rep = SimpleNamespace(positions=[
        SimpleNamespace(ticker="AAA", quantity=10.0, avg_price=100.0, value_rub=1200.0,
                        sector="Банки"),
        SimpleNamespace(ticker="USD", quantity=0.0, avg_price=None, value_rub=500.0,
                        sector="Кэш"),
    ])
    assert _cost_basis_rub(rep) == 1500.0     # 10·100 + 500 кэша


def test_cost_basis_rub_none_when_avg_missing():
    """Хоть одна акция без avg_price → база None (неполная → P&L был бы искажён)."""
    rep = SimpleNamespace(positions=[
        SimpleNamespace(ticker="AAA", quantity=10.0, avg_price=100.0, value_rub=1000.0,
                        sector="Банки"),
        SimpleNamespace(ticker="BBB", quantity=5.0, avg_price=None, value_rub=500.0,
                        sector="Нефтегаз"),
    ])
    assert _cost_basis_rub(rep) is None


def test_report_value_series_from_snapshots(monkeypatch):
    """≥2 снимка → стоимость/ P&L во времени берутся из ФАКТА (источник 'snapshots')."""
    ds = _dates(40)
    asset_a = SimpleNamespace(id=1, ticker="AAA", company=None)
    pos_a = SimpleNamespace(quantity=10.0, avg_price=90.0)
    levels_a = {d: 100.0 + i for i, d in enumerate(ds)}
    attr_ok = SimpleNamespace(error=None, betas={"market": 1.0}, r2=0.5)
    regime = SimpleNamespace(error=None, current="спокойный")
    snaps = [(date(2026, 2, 1), 1000.0, 900.0), (date(2026, 2, 2), 1050.0, 900.0)]

    with (
        patch("geoanalytics.analytics.portfolio.PortfolioRepository",
              return_value=_mock_repo([(asset_a, pos_a)])),
        patch("geoanalytics.analytics.portfolio._price_levels", return_value=levels_a),
        patch("geoanalytics.analytics.portfolio._asset_returns", return_value={}),
        patch("geoanalytics.analytics.portfolio.attribute_asset", return_value=attr_ok),
        patch("geoanalytics.analytics.portfolio.news_pressure", return_value=0.0),
        patch("geoanalytics.analytics.portfolio.latest_momentum", return_value=0.0),
        patch("geoanalytics.analytics.portfolio._cash_positions", return_value=[]),
        patch("geoanalytics.analytics.portfolio.market_regimes", return_value=regime),
        patch("geoanalytics.analytics.portfolio.PortfolioSnapshotRepository",
              return_value=SimpleNamespace(history=lambda **k: snaps)),
    ):
        rep = portfolio_report(MagicMock())

    assert rep.value_series_source == "snapshots"
    assert rep.value_series == [(date(2026, 2, 1), 1000.0), (date(2026, 2, 2), 1050.0)]
    assert rep.pnl_series == [(date(2026, 2, 1), 100.0), (date(2026, 2, 2), 150.0)]


def test_report_empty_portfolio():
    """Пустой портфель → error, без падения."""
    with (patch("geoanalytics.analytics.portfolio.PortfolioRepository",
                return_value=_mock_repo([])),
          patch("geoanalytics.analytics.portfolio._cash_positions", return_value=[])):
        rep = portfolio_report(MagicMock())
    assert rep.error is not None
    assert "пуст" in rep.error


def test_report_live_price_overrides_valuation():
    """live_prices задаёт цену оценки (стоимость/P&L) поверх EOD-закрытия; риск — по истории."""
    ds = _dates(40)
    asset = SimpleNamespace(id=1, ticker="AAA")
    pos = SimpleNamespace(quantity=10.0, avg_price=100.0)
    levels = {d: 100.0 + i for i, d in enumerate(ds)}   # EOD-закрытие = 139.0

    attr_ok = SimpleNamespace(error=None, betas={"market": 1.0}, r2=0.5)
    regime = SimpleNamespace(error=None, current="спокойный")

    with (
        patch("geoanalytics.analytics.portfolio.PortfolioRepository",
              return_value=_mock_repo([(asset, pos)])),
        patch("geoanalytics.analytics.portfolio._price_levels", return_value=levels),
        patch("geoanalytics.analytics.portfolio._asset_returns", return_value={}),
        patch("geoanalytics.analytics.portfolio.attribute_asset", return_value=attr_ok),
        patch("geoanalytics.analytics.portfolio.news_pressure", return_value=0.2),
        patch("geoanalytics.analytics.portfolio.latest_momentum", return_value=0.1),
        patch("geoanalytics.analytics.portfolio._cash_positions", return_value=[]),
        patch("geoanalytics.analytics.portfolio.PortfolioSnapshotRepository",
              return_value=SimpleNamespace(history=lambda **k: [])),
        patch("geoanalytics.analytics.portfolio.market_regimes", return_value=regime),
    ):
        rep = portfolio_report(MagicMock(), live_prices={"AAA": 150.0})

    p = rep.positions[0]
    assert p.last_close == 150.0                 # живая цена, не EOD 139.0
    assert rep.total_value_rub == 1500.0         # 150·10
    assert p.pnl_pct == 50.0                     # от avg_price 100 по живой цене


def test_cash_positions_valuation():
    """RUB по номиналу, прочие по курсу ЦБ; валюта без курса пропускается."""
    from geoanalytics.analytics.portfolio import _cash_positions

    cash_repo = MagicMock()
    cash_repo.list_balances.return_value = [("RUB", 5000.0), ("USD", 100.0), ("XXX", 10.0)]
    with (
        patch("geoanalytics.analytics.portfolio.CashBalanceRepository",
              return_value=cash_repo),
        patch("geoanalytics.analytics.portfolio._latest_fx_rate",
              side_effect=lambda _s, c: 70.0 if c == "USD" else None),
    ):
        out = _cash_positions(MagicMock(), None)
    by = {c["ccy"]: c for c in out}
    assert by["RUB"]["rub"] == 5000.0 and by["RUB"]["rate"] == 1.0
    assert by["USD"]["rub"] == 7000.0          # 100 × 70
    assert "XXX" not in by                       # нет курса — пропущена


def test_report_rub_cash_in_value_and_dilutes_risk():
    """RUB-кэш (база) входит в стоимость/состав (сектор «Кэш», 0% риска) и разбавляет веса акций."""
    ds = _dates(40)
    asset = SimpleNamespace(id=1, ticker="AAA")
    pos = SimpleNamespace(quantity=10.0, avg_price=None)
    levels = {d: 100.0 + i for i, d in enumerate(ds)}   # EOD 139 → AAA 1390 ₽
    attr_ok = SimpleNamespace(error=None, betas={"market": 1.0}, r2=0.5)
    regime = SimpleNamespace(error=None, current="спокойный")
    cash = [{"ccy": "RUB", "amount": 7000.0, "rub": 7000.0, "rate": 1.0}]

    with (
        patch("geoanalytics.analytics.portfolio.PortfolioRepository",
              return_value=_mock_repo([(asset, pos)])),
        patch("geoanalytics.analytics.portfolio._price_levels", return_value=levels),
        patch("geoanalytics.analytics.portfolio._asset_returns", return_value={}),
        patch("geoanalytics.analytics.portfolio.attribute_asset", return_value=attr_ok),
        patch("geoanalytics.analytics.portfolio.news_pressure", return_value=0.2),
        patch("geoanalytics.analytics.portfolio.latest_momentum", return_value=0.1),
        patch("geoanalytics.analytics.portfolio.market_regimes", return_value=regime),
        patch("geoanalytics.analytics.portfolio._cash_positions", return_value=cash),
        patch("geoanalytics.analytics.portfolio.PortfolioSnapshotRepository",
              return_value=SimpleNamespace(history=lambda **k: [])),
    ):
        rep = portfolio_report(MagicMock())

    assert rep.total_value_rub == 8390.0               # 1390 акций + 7000 кэша
    by = {p.ticker: p for p in rep.positions}
    assert by["RUB"].value_rub == 7000.0 and by["RUB"].sector == "Кэш"
    assert by["RUB"].risk_contribution_pct == 0.0      # рубли — база, вне риска
    assert abs(by["AAA"].weight_pct - 16.57) < 0.5     # 1390/8390, разбавлено кэшом
    assert dict(rep.sector_alloc)["Кэш"] > 80          # кэш доминирует в аллокации


def test_report_foreign_cash_carries_fx_risk():
    """Валютный кэш (USD) несёт FX-риск: реальная доходность курса ЦБ → ненулевой вклад в риск."""
    ds = _dates(40)
    asset = SimpleNamespace(id=1, ticker="AAA")
    pos = SimpleNamespace(quantity=10.0, avg_price=None)
    levels = {d: 100.0 + i for i, d in enumerate(ds)}
    # Курс USD/RUB с заметной волатильностью по тем же датам, что и доходности акции.
    fx_ret = {d: (0.02 if i % 2 else -0.015) for i, d in enumerate(ds[1:], start=1)}
    attr_ok = SimpleNamespace(error=None, betas={"market": 1.0}, r2=0.5)
    regime = SimpleNamespace(error=None, current="спокойный")
    cash = [{"ccy": "USD", "amount": 100.0, "rub": 7000.0, "rate": 70.0}]

    with (
        patch("geoanalytics.analytics.portfolio.PortfolioRepository",
              return_value=_mock_repo([(asset, pos)])),
        patch("geoanalytics.analytics.portfolio._price_levels", return_value=levels),
        patch("geoanalytics.analytics.portfolio._asset_returns", return_value={}),
        patch("geoanalytics.analytics.portfolio.attribute_asset", return_value=attr_ok),
        patch("geoanalytics.analytics.portfolio.news_pressure", return_value=0.0),
        patch("geoanalytics.analytics.portfolio.latest_momentum", return_value=0.0),
        patch("geoanalytics.analytics.portfolio.market_regimes", return_value=regime),
        patch("geoanalytics.analytics.portfolio._cash_positions", return_value=cash),
        patch("geoanalytics.analytics.portfolio._fx_returns_by_date", return_value=fx_ret),
        patch("geoanalytics.analytics.portfolio.PortfolioSnapshotRepository",
              return_value=SimpleNamespace(history=lambda **k: [])),
    ):
        rep = portfolio_report(MagicMock())

    by = {p.ticker: p for p in rep.positions}
    assert by["USD"].sector == "Кэш"
    assert "риск по курсу" in by["USD"].note          # помечен как валютный риск
    assert by["USD"].risk_contribution_pct not in (None, 0.0)   # несёт FX-риск


def test_report_cash_only_portfolio():
    """Портфель только из кэша → стоимость есть, без риска, без ошибки."""
    cash = [{"ccy": "RUB", "amount": 5000.0, "rub": 5000.0, "rate": 1.0}]
    regime = SimpleNamespace(error=None, current="спокойный")
    with (
        patch("geoanalytics.analytics.portfolio.PortfolioRepository",
              return_value=_mock_repo([])),
        patch("geoanalytics.analytics.portfolio._cash_positions", return_value=cash),
        patch("geoanalytics.analytics.portfolio.PortfolioSnapshotRepository",
              return_value=SimpleNamespace(history=lambda **k: [])),
        patch("geoanalytics.analytics.portfolio.market_regimes", return_value=regime),
    ):
        rep = portfolio_report(MagicMock())
    assert rep.error is None and rep.total_value_rub == 5000.0
    assert rep.positions[0].ticker == "RUB"
    assert rep.daily_vol_pct is None                   # нет риск-серии


def test_report_value_series_sector_alloc_and_risk_contribution():
    """Новые срезы качественного просмотра: стоимость во времени, аллокация по секторам,
    вклад позиций в риск (Σ≈100)."""
    ds = _dates(40)
    bank = SimpleNamespace(sector=SimpleNamespace(name="Банки"))
    asset_a = SimpleNamespace(id=1, ticker="AAA", company=SimpleNamespace(sector=bank.sector))
    asset_b = SimpleNamespace(id=2, ticker="BBB", company=None)  # сектор не привязан → «—»
    pos_a = SimpleNamespace(quantity=10.0, avg_price=None)
    pos_b = SimpleNamespace(quantity=5.0, avg_price=None)
    levels_a = {d: 100.0 + i for i, d in enumerate(ds)}
    levels_b = {d: 200.0 - 0.4 * i for i, d in enumerate(ds)}

    attr_ok = SimpleNamespace(error=None, betas={"market": 1.0}, r2=0.5)
    regime = SimpleNamespace(error=None, current="спокойный")

    with (
        patch("geoanalytics.analytics.portfolio.PortfolioRepository",
              return_value=_mock_repo([(asset_a, pos_a), (asset_b, pos_b)])),
        patch("geoanalytics.analytics.portfolio._price_levels",
              side_effect=lambda _s, aid: levels_a if aid == 1 else levels_b),
        patch("geoanalytics.analytics.portfolio._asset_returns", return_value={}),
        patch("geoanalytics.analytics.portfolio.attribute_asset", return_value=attr_ok),
        patch("geoanalytics.analytics.portfolio.news_pressure", return_value=0.2),
        patch("geoanalytics.analytics.portfolio.latest_momentum", return_value=0.1),
        patch("geoanalytics.analytics.portfolio._cash_positions", return_value=[]),
        patch("geoanalytics.analytics.portfolio.PortfolioSnapshotRepository",
              return_value=SimpleNamespace(history=lambda **k: [])),
        patch("geoanalytics.analytics.portfolio.market_regimes", return_value=regime),
    ):
        rep = portfolio_report(MagicMock())

    # Стоимость во времени: последняя точка совпадает с текущей стоимостью.
    assert rep.value_series
    assert abs(rep.value_series[-1][1] - rep.total_value_rub) < 1.0
    # Аллокация: «Банки» (AAA) + «—» (BBB), сумма ≈ 100%.
    alloc = dict(rep.sector_alloc)
    assert "Банки" in alloc and "—" in alloc
    assert abs(sum(alloc.values()) - 100.0) < 0.5
    # Вклад в риск суммируется к 100% и проставлен на позициях.
    rc = {p.ticker: p.risk_contribution_pct for p in rep.positions}
    assert all(v is not None for v in rc.values())
    assert abs(sum(rc.values()) - 100.0) < 0.5
    assert {p.sector for p in rep.positions} == {"Банки", None}


def test_report_position_without_prices():
    """Позиция без цен — note, не в стоимости; вторая считается нормально."""
    ds = _dates(40)
    asset_a = SimpleNamespace(id=1, ticker="AAA")
    asset_b = SimpleNamespace(id=2, ticker="BBB")
    pos = SimpleNamespace(quantity=10.0, avg_price=None)
    levels_a = {d: 100.0 + i for i, d in enumerate(ds)}

    attr_ok = SimpleNamespace(error=None, betas={"market": 1.1}, r2=0.5)
    regime = SimpleNamespace(error=None, current="спокойный")

    with (
        patch("geoanalytics.analytics.portfolio.PortfolioRepository",
              return_value=_mock_repo([(asset_a, pos), (asset_b, pos)])),
        patch("geoanalytics.analytics.portfolio._price_levels",
              side_effect=lambda _s, aid: levels_a if aid == 1 else {}),
        patch("geoanalytics.analytics.portfolio._asset_returns",
              return_value={}),
        patch("geoanalytics.analytics.portfolio.attribute_asset",
              return_value=attr_ok),
        patch("geoanalytics.analytics.portfolio.news_pressure", return_value=0.2),
        patch("geoanalytics.analytics.portfolio.latest_momentum", return_value=0.1),
        patch("geoanalytics.analytics.portfolio._cash_positions", return_value=[]),
        patch("geoanalytics.analytics.portfolio.PortfolioSnapshotRepository",
              return_value=SimpleNamespace(history=lambda **k: [])),
        patch("geoanalytics.analytics.portfolio.market_regimes",
              return_value=regime),
    ):
        rep = portfolio_report(MagicMock())

    assert rep.error is None
    assert rep.total_value_rub == 1390.0  # только AAA: 139·10
    by_ticker = {p.ticker: p for p in rep.positions}
    assert by_ticker["BBB"].note is not None
    assert by_ticker["BBB"].value_rub is None
    assert by_ticker["AAA"].weight_pct == 100.0
    assert rep.exposure["market"] == 1.1
    assert rep.regime == "спокойный"
    assert rep.n_obs >= 20
    assert rep.var95_1d_pct is not None
