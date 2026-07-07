"""Трек 2 / Пул 9 / C: тесты портфельного риска фьючерсного бука (чистые ядра, без БД)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from geoanalytics.futrader.portfolio_risk import (
    _book_returns,
    contract_notional,
    correlation_scale,
    expected_shortfall,
    exposure_by_code,
    gross_net,
    portfolio_risk_report,
)


@dataclass
class _Spec:
    tick_size: float = 0.01
    tick_value: float = 1.0


@dataclass
class _Pos:
    asset_code: str
    net_qty: int
    last_price: float | None = 100.0


class TestExpectedShortfall:
    def test_none_when_few(self):
        assert expected_shortfall([-0.01] * 10) is None

    def test_tail_average(self):
        # 100 доходностей: −0.10 в худшем 1%, остальные 0 → ES95 усредняет худшие 5: один −0.10
        rets = [-0.10] + [0.0] * 99
        es = expected_shortfall(rets, 0.95)
        # худшие 5 = [-0.10, 0,0,0,0]; убытки только −0.10 → среднее модулей убытков = 0.10
        assert es == 0.10

    def test_no_losses_zero(self):
        assert expected_shortfall([0.01] * 50, 0.95) == 0.0


class TestExposure:
    def test_contract_notional(self):
        assert contract_notional(100.0, _Spec(tick_size=0.01, tick_value=1.0)) == 10_000.0

    def test_exposure_signed(self):
        positions = [_Pos("BR", 2, 100.0), _Pos("RTS", -1, 50.0)]
        specs = {"BR": _Spec(), "RTS": _Spec()}
        exp = exposure_by_code(positions, specs)
        assert exp["BR"] == 2 * 10_000.0
        assert exp["RTS"] == -1 * 5_000.0

    def test_gross_net(self):
        gross, net = gross_net({"BR": 20_000.0, "RTS": -5_000.0})
        assert gross == 25_000.0 and net == 15_000.0


class TestBookReturns:
    def test_weighted_sum_on_common_dates(self):
        d0 = date(2026, 1, 1)
        rets = {"A": {d0: 0.01, d0 + timedelta(days=1): 0.02},
                "B": {d0: -0.01, d0 + timedelta(days=1): 0.00}}
        book = _book_returns({"A": 0.5, "B": 0.5}, rets)
        assert book[d0] == 0.5 * 0.01 + 0.5 * -0.01
        assert round(book[d0 + timedelta(days=1)], 6) == 0.01


class TestCorrelationScale:
    def test_penalizes_concentration(self):
        # уже лонг RTS; BR сильно скоррелирован (+0.8) с RTS; новый лонг BR усиливает ставку → штраф
        exposure = {"RTS": 10_000.0}
        corr = {("BR", "RTS"): 0.8}
        assert correlation_scale("BR", 1, exposure, corr, threshold=0.6, penalty=0.5) == 0.5

    def test_no_penalty_for_diversifying(self):
        # новый ШОРТ BR против лонга RTS (коррелированы) — снижает концентрацию → без штрафа
        exposure = {"RTS": 10_000.0}
        corr = {("BR", "RTS"): 0.8}
        assert correlation_scale("BR", -1, exposure, corr, threshold=0.6, penalty=0.5) == 1.0

    def test_low_correlation_ignored(self):
        exposure = {"RTS": 10_000.0}
        corr = {("BR", "RTS"): 0.2}
        assert correlation_scale("BR", 1, exposure, corr, threshold=0.6, penalty=0.5) == 1.0


class TestPortfolioRiskReport:
    def test_empty_book(self):
        rep = portfolio_risk_report([], {}, {})
        assert rep.n_instruments == 0
        assert rep.var_pct is None

    def test_var_es_on_book(self):
        d0 = date(2026, 1, 1)
        rets = {"BR": {d0 + timedelta(days=i): (-0.05 if i == 0 else 0.001) for i in range(40)},
                "RTS": {d0 + timedelta(days=i): (-0.04 if i == 0 else 0.001) for i in range(40)}}
        positions = [_Pos("BR", 1, 100.0), _Pos("RTS", 1, 100.0)]
        specs = {"BR": _Spec(), "RTS": _Spec()}
        rep = portfolio_risk_report(positions, rets, specs, level=0.95)
        assert rep.n_instruments == 2
        assert rep.gross_exposure > 0
        assert rep.var_pct is not None and rep.es_pct is not None
