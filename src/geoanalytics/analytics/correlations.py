"""Корреляции доходностей актива с факторами рынка.

Считаем корреляцию Пирсона дневных доходностей актива с:
- курсом USD/RUB (валютный фактор);
- средней доходностью пиров по сектору (отраслевой фактор);
- нефтью Brent и драгметаллами (золото/серебро), если данные есть (сырьевые факторы);
- любым другим макро-рядом из macro_series при наличии.

Ряды выравниваются по общим датам. Пирсон реализован на чистом Python (тестируемо).
"""

from __future__ import annotations

from datetime import date
from math import ceil

from sqlalchemy import asc, select
from sqlalchemy.orm import Session

from geoanalytics.context.graph import factors_for_asset
from geoanalytics.storage.models import Asset, FxRate, MacroSeries, Price

# Минимум совпадающих точек, чтобы корреляция была осмысленной.
MIN_POINTS = 20
# Б14: при разрыве между соседними датами больше этого (дней) «1-шаговая» доходность
# фактически многодневная — горизонты смешиваются. Длинные дыры выкидываем; выходные/
# единичный праздник (Fri→Mon = 3 дня, +1 на праздник) сохраняем. Это ДЕФОЛТ для всех
# потребителей доходностей (корреляции/атрибуция/режимы/whatif/портфель) — Б14 закрыта
# глобально; max_gap_days=None — явный отказ от гарда.
_MAX_RETURN_GAP_DAYS = 4
# Б14: дату включаем в средний пир-фактор, только если торговала хотя бы такая доля пиров —
# иначе тонкий день (1 неликвидный пир) получает тот же вес, что полный день.
_PEER_MIN_COVERAGE = 0.5


def pearson(x: list[float], y: list[float]) -> float | None:
    """Коэффициент корреляции Пирсона. None, если посчитать нельзя."""
    n = len(x)
    if n < 2 or n != len(y):
        return None
    mx = sum(x) / n
    my = sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    denom = (sxx * syy) ** 0.5
    if denom == 0:
        return None
    return round(sxy / denom, 3)


def _returns_by_date(series: dict[date, float],
                     *, max_gap_days: int | None = _MAX_RETURN_GAP_DAYS) -> dict[date, float]:
    """Дневные доходности по упорядоченным датам ряда уровней.

    `max_gap_days` (Б14): пропустить доходность, если разрыв между соседними датами больше
    порога (многодневный гэп смешивает горизонты). Дефолт `_MAX_RETURN_GAP_DAYS` применяется
    ко всем потребителям (корреляции/атрибуция/режимы/whatif/портфель); None — отказ от гарда.
    """
    dates = sorted(series)
    out: dict[date, float] = {}
    for prev, cur in zip(dates, dates[1:], strict=False):
        if max_gap_days is not None and (cur - prev).days > max_gap_days:
            continue
        p = series[prev]
        if p:
            out[cur] = (series[cur] - p) / p
    return out


def _aligned(a: dict[date, float], b: dict[date, float]) -> tuple[list[float], list[float]]:
    """Выравнивает два словаря дата→значение по общим датам."""
    common = sorted(set(a) & set(b))
    return [a[d] for d in common], [b[d] for d in common]


def _price_levels(session: Session, asset_id: int) -> dict[date, float]:
    rows = session.execute(
        select(Price.ts, Price.close).where(
            Price.asset_id == asset_id, Price.interval == "1d"
        ).order_by(asc(Price.ts))
    )
    return {ts.date(): float(c) for ts, c in rows}


def _fx_levels(session: Session, currency: str) -> dict[date, float]:
    rows = session.execute(
        select(FxRate.ts, FxRate.value).where(FxRate.currency == currency)
        .order_by(asc(FxRate.ts))
    )
    return {ts.date(): float(v) for ts, v in rows}


def _macro_levels(session: Session, indicator: str) -> dict[date, float]:
    rows = session.execute(
        select(MacroSeries.ts, MacroSeries.value)
        .where(MacroSeries.indicator == indicator).order_by(asc(MacroSeries.ts))
    )
    return {ts.date(): float(v) for ts, v in rows}


def _cross_levels(session: Session, base: str, quote: str) -> dict[date, float]:
    """Кросс-курс base/quote из рублёвых курсов ЦБ: (base/RUB)/(quote/RUB).

    USD/EUR из тех же официальных серий — отдельного источника не нужно.
    """
    base_rub = _fx_levels(session, base)
    quote_rub = _fx_levels(session, quote)
    return {d: base_rub[d] / quote_rub[d]
            for d in set(base_rub) & set(quote_rub) if quote_rub[d]}


# Эмпирический лаг учётных цен ЦБ к биржевому дню (см. ниже): 2 торговых позиции.
METAL_PUBLICATION_LAG = 2


def shift_positions(levels: dict[date, float], k: int,
                    *, ffill_tail: bool = True) -> dict[date, float]:
    """Сдвиг ряда на k позиций НАЗАД по его собственным датам (k>0).

    Значение с даты i+k встаёт на дату i — ряд «приближается» к рынку без
    календарного шума выходных. При ffill_tail последние k дат заполняются
    последним известным значением (доходность там 0), чтобы фактор не выпадал
    из атрибуции текущего дня по покрытию. Чистая функция.
    """
    dates = sorted(levels)
    if k <= 0 or len(dates) <= k:
        return dict(levels)
    out = {dates[i]: levels[dates[i + k]] for i in range(len(dates) - k)}
    if ffill_tail:
        last = levels[dates[-1]]
        for d in dates[-k:]:
            out[d] = last
    return out


def _world_metal_levels(session: Session, indicator: str) -> dict[date, float]:
    """Мировая цена металла в USD из учётной цены ЦБ, выровненная к биржевому дню.

    Учётная цена ЦБ (₽/г) на дату T строится из фиксинга LBMA и официального
    курса, установленных ЗАРАНЕЕ: эмпирически ряд опаздывает к бирже на
    2 торговых дня, а на лаге 0 корреляция с USD/RUB 0.79 — это валютная
    компонента, не металл (проверено 2026-06-13: PLZL~gold r=+0.30 на k=-2
    после деления на курс, контроль SBER +0.06). Поэтому: делим на официальный
    USD/RUB той же даты (конвенции публикации совпадают) и сдвигаем на
    METAL_PUBLICATION_LAG позиций. Рублёвую экспозицию металла OLS соберёт
    из пары (metal, usd_rub).
    """
    metal = _macro_levels(session, indicator)
    usd = _fx_levels(session, "USD")
    levels = {d: metal[d] / usd[d] for d in set(metal) & set(usd) if usd[d]}
    return shift_positions(levels, METAL_PUBLICATION_LAG)


def _peer_returns(session: Session, peer_tickers: list[str], *,
                  min_coverage: float = 0.0,
                  max_gap_days: int | None = _MAX_RETURN_GAP_DAYS) -> dict[date, float]:
    """Средняя дневная доходность по пирам сектора.

    Б14: `min_coverage` — минимальная доля пиров с данными на дату, чтобы дату включить
    (иначе тонкий день с одним неликвидным пиром искажает фактор; дефолт 0.0 = без фильтра,
    его задаёт только `correlate_asset`). `max_gap_days` гардит горизонты (дефолт — глобальный).
    """
    if not peer_tickers:
        return {}
    sums: dict[date, float] = {}
    counts: dict[date, int] = {}
    peers = list(session.scalars(select(Asset).where(Asset.ticker.in_(peer_tickers))))
    for peer in peers:
        rets = _returns_by_date(_price_levels(session, peer.id), max_gap_days=max_gap_days)
        for d, r in rets.items():
            sums[d] = sums.get(d, 0.0) + r
            counts[d] = counts.get(d, 0) + 1
    threshold = max(1, ceil(len(peers) * min_coverage)) if peers else 1
    return {d: sums[d] / counts[d] for d in sums if counts[d] >= threshold}


def correlate_asset(session: Session, asset: Asset) -> dict[str, float]:
    """Корреляции доходностей актива с ключевыми факторами."""
    asset_rets = _returns_by_date(_price_levels(session, asset.id))
    if len(asset_rets) < MIN_POINTS:
        return {}

    out: dict[str, float] = {}

    def add(name: str, factor_levels_or_rets: dict[date, float], *, is_returns: bool) -> None:
        factor_rets = (factor_levels_or_rets if is_returns
                       else _returns_by_date(factor_levels_or_rets))
        xs, ys = _aligned(asset_rets, factor_rets)
        if len(xs) >= MIN_POINTS:
            r = pearson(xs, ys)
            if r is not None:
                out[name] = r

    add("usd_rub", _fx_levels(session, "USD"), is_returns=False)
    add("usd_eur", _cross_levels(session, "USD", "EUR"), is_returns=False)
    add("brent", _macro_levels(session, "brent"), is_returns=False)
    # Металлы — мировые цены в USD из учётных цен ЦБ с поправкой лага публикации.
    add("gold", _world_metal_levels(session, "gold"), is_returns=False)
    add("silver", _world_metal_levels(session, "silver"), is_returns=False)
    add("platinum", _world_metal_levels(session, "platinum"), is_returns=False)
    add("palladium", _world_metal_levels(session, "palladium"), is_returns=False)

    factors = factors_for_asset(session, asset)
    add("sector_peers",
        _peer_returns(session, factors.peers, min_coverage=_PEER_MIN_COVERAGE),
        is_returns=True)

    return out
