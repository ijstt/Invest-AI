"""Тесты факторной атрибуции (G3): восстановление бет на синтетике, отбор факторов."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from geoanalytics.analytics.attribution import ols_attribution

_RNG = np.random.default_rng(7)


def _dates(n: int, start: date = date(2025, 1, 1)) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def _series(dates: list[date], values) -> dict[date, float]:
    return dict(zip(dates, (float(v) for v in values), strict=True))


def _synthetic(n: int = 300, beta_mkt: float = 1.2, beta_fx: float = -0.5,
               noise: float = 0.001):
    """Актив = beta_mkt·рынок + beta_fx·fx + шум; последний день — целевой."""
    days = _dates(n)
    mkt = _RNG.normal(0, 0.01, n)
    fx = _RNG.normal(0, 0.008, n)
    asset = beta_mkt * mkt + beta_fx * fx + _RNG.normal(0, noise, n)
    return (_series(days, asset),
            {"market": _series(days, mkt), "usd_rub": _series(days, fx)})


class TestOlsAttribution:
    def test_recovers_known_betas(self):
        asset, factors = _synthetic()
        r = ols_attribution(asset, factors)
        assert r is not None
        assert abs(r.betas["market"] - 1.2) < 0.1
        assert abs(r.betas["usd_rub"] + 0.5) < 0.1
        assert r.r2 > 0.9
        assert r.n_obs == 250  # дефолтное окно при 299 доступных днях

    def test_day_decomposition_sums_to_return(self):
        asset, factors = _synthetic()
        r = ols_attribution(asset, factors)
        total = r.alpha_pct + sum(r.contributions_pct.values()) + r.idio_pct
        assert abs(total - r.asset_return_pct) < 0.02  # округление сотых

    def test_target_day_excluded_from_fit(self):
        # Аномальный целевой день не должен менять беты (leave-one-out).
        asset, factors = _synthetic()
        day = max(asset)
        calm = ols_attribution(asset, factors, day=day)
        asset[day] = 0.30  # +30% — заведомая идиосинкразия
        shocked = ols_attribution(asset, factors, day=day)
        assert shocked.betas == calm.betas
        assert shocked.idio_pct > 25

    def test_short_factor_is_dropped(self):
        # Фактор с 9 точками (живой Brent) не должен убить регрессию.
        asset, factors = _synthetic()
        days = sorted(asset)
        factors["brent"] = _series(days[-9:], _RNG.normal(0, 0.01, 9))
        r = ols_attribution(asset, factors)
        assert r is not None
        assert "brent" not in r.betas
        assert set(r.betas) == {"market", "usd_rub"}

    def test_factor_without_target_day_dropped(self):
        asset, factors = _synthetic()
        day = max(asset)
        del factors["usd_rub"][day]
        r = ols_attribution(asset, factors, day=day)
        assert "usd_rub" not in r.betas

    def test_not_enough_data(self):
        asset, factors = _synthetic(n=30)
        assert ols_attribution(asset, factors) is None

    def test_empty_input(self):
        assert ols_attribution({}, {}) is None
