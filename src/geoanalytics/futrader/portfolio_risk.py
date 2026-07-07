"""Трек 2 / Пул 9 / C: портфельный риск фьючерсного «бука» (институциональный слой).

То, что превращает «30 независимых ботов» в ПОРТФЕЛЬ: ковариационно-осознанная картина риска по
всем открытым позициям сразу — портфельный VaR/ES, риск-контрибьюторы (Euler), брутто/нетто
экспозиция, корреляции, — и корреляционно-осознанный лимит на новые входы (не пирамидить
скоррелированные BR+RTS в одну сторону).

Переиспользуем ЧИСТЫЕ ядра Трека 1 (работают на generic `dict[date,float]` доходностях):
`portfolio.historical_var/_risk_contributions/correlation_matrix`, `correlations._returns_by_date`.
Expected Shortfall (CVaR) добавляем здесь — в Треке 1 его не было. Чистые функции (тестируемы);
DB-сбор рядов — тонкой обёрткой.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


def expected_shortfall(returns: list[float], level: float = 0.95) -> float | None:
    """Expected Shortfall (CVaR): средний убыток В ХВОСТЕ за VaR-квантилем (≥0). None если мало.

    ES честнее VaR на толстых хвостах RU: не «порог потерь», а ОЖИДАЕМЫЙ убыток, когда порог пробит.
    Берём худшие (1−level) доходностей и усредняем их модуль (только отрицательные хвосты)."""
    if len(returns) < 20:
        return None
    ordered = sorted(returns)
    k = max(1, int(round((1.0 - level) * len(ordered))))
    tail = ordered[:k]
    losses = [-r for r in tail if r < 0]
    if not losses:
        return 0.0
    return sum(losses) / len(losses)


def contract_notional(price: float, spec) -> float:
    """Полная рублёвая стоимость 1 контракта: (price/tick_size)·tick_value (вес экспозиции)."""
    ts = getattr(spec, "tick_size", 0.0) or 0.0
    if ts <= 0:
        return 0.0
    return (price / ts) * (getattr(spec, "tick_value", 0.0) or 0.0)


def exposure_by_code(positions, spec_map: dict) -> dict[str, float]:
    """Знаковая рублёвая экспозиция по инструменту: net_qty·notional (лонг +, шорт −)."""
    out: dict[str, float] = {}
    for p in positions:
        spec = spec_map.get(p.asset_code)
        if spec is not None and p.net_qty and p.last_price:
            out[p.asset_code] = p.net_qty * contract_notional(p.last_price, spec)
    return out


def gross_net(exposure: dict[str, float]) -> tuple[float, float]:
    """Брутто (Σ|exp|) и нетто (Σexp) рублёвая экспозиция бука."""
    gross = sum(abs(v) for v in exposure.values())
    net = sum(exposure.values())
    return gross, net


def _book_returns(weights: dict[str, float],
                  rets: dict[str, dict[date, float]]) -> dict[date, float]:
    """Доходность бука по датам: Σ weight_i·r_i(date) на общих датах (знаковые веса, нетто OK)."""
    if not weights:
        return {}
    common: set[date] | None = None
    for code in weights:
        days = set(rets.get(code, {}))
        common = days if common is None else (common & days)
    if not common:
        return {}
    out: dict[date, float] = {}
    for d in sorted(common):
        out[d] = sum(weights[c] * rets[c][d] for c in weights)
    return out


def correlation_scale(code: str, direction: int, exposure: dict[str, float],
                      corr: dict[tuple[str, str], float], *, threshold: float = 0.6,
                      penalty: float = 0.5) -> float:
    """Корр-осознанный множитель размера ∈[penalty,1]: ужать вход, если он НАРАЩИВАЕТ уже имеющуюся
    скоррелированную одностороннюю экспозицию (концентрация риска), иначе 1.0 (диверсификация).

    Для каждого инструмента с открытой позицией: если |corr|>threshold и новый вход усиливает
    совокупную направленную ставку (corr·sign(exp_j) совпадает с direction) — копим штраф."""
    worst = 1.0
    for other, exp_j in exposure.items():
        if other == code or exp_j == 0:
            continue
        c = corr.get((code, other)) or corr.get((other, code))
        if c is None or abs(c) < threshold:
            continue
        aligned = (1 if c > 0 else -1) * (1 if exp_j > 0 else -1)
        if aligned == direction:                      # вход усиливает скоррелированную ставку
            worst = min(worst, penalty)
    return worst


@dataclass
class PortfolioRiskReport:
    n_instruments: int = 0
    gross_exposure: float = 0.0
    net_exposure: float = 0.0
    var_pct: float | None = None
    es_pct: float | None = None
    contributions: dict = field(default_factory=dict)
    top_correlations: list = field(default_factory=list)
    note: str = ""


def portfolio_risk_report(positions, rets: dict[str, dict[date, float]], spec_map: dict, *,
                          level: float = 0.95) -> PortfolioRiskReport:
    """Портфельный VaR/ES + риск-контрибьюторы (Euler) + брутто/нетто + топ-корреляции (чистая)."""
    from geoanalytics.analytics.portfolio import (
        _risk_contributions,
        correlation_matrix,
        historical_var,
    )

    exposure = exposure_by_code(positions, spec_map)
    rep = PortfolioRiskReport(n_instruments=len(exposure))
    gross, net = gross_net(exposure)
    rep.gross_exposure = round(gross, 2)
    rep.net_exposure = round(net, 2)
    if gross <= 0:
        rep.note = "нет открытых позиций для портфельного риска"
        return rep
    weights = {c: exposure[c] / gross for c in exposure}                 # знаковые, Σ|w|=1
    book = _book_returns(weights, rets)
    series = list(book.values())
    var = historical_var(series, level)
    rep.var_pct = round(var * 100.0, 3) if var is not None else None
    es = expected_shortfall(series, level)
    rep.es_pct = round(es * 100.0, 3) if es is not None else None
    if book and len([c for c in weights if c in rets]) >= 2:
        try:
            rep.contributions = {c: round(v, 1) for c, v in
                                 _risk_contributions(weights, rets, book).items()}
        except Exception:  # noqa: BLE001 — вырожденная ковариация не валит отчёт
            rep.contributions = {}
    cm = correlation_matrix({c: rets[c] for c in weights if c in rets})
    rep.top_correlations = sorted(
        ([f"{a}/{b}", v] for (a, b), v in cm.items()),
        key=lambda kv: abs(kv[1]), reverse=True)[:3]
    rep.note = f"VaR/ES {int(level * 100)}% на {len(series)} дн. доходностей бука"
    return rep


def build_instrument_returns(session, tickers, *, interval: str = "1d",
                             days: int = 180) -> dict[str, dict[date, float]]:
    """Дневные доходности непрерывных рядов инструментов (ключ — asset_code). DB-обёртка."""
    from geoanalytics.analytics.correlations import _returns_by_date
    from geoanalytics.futrader.continuous import continuous_series
    from geoanalytics.futrader.data import _asset_code_for

    out: dict[str, dict[date, float]] = {}
    for tk in tickers:
        try:
            series = continuous_series(session, tk, interval=interval)
        except Exception:  # noqa: BLE001 — один инструмент не валит остальные
            continue
        bars = series.bars[-days:] if days else series.bars
        levels = {b.ts.date(): b.close for b in bars}
        if len(levels) >= 2:
            out[_asset_code_for(tk)] = _returns_by_date(levels)
    return out
