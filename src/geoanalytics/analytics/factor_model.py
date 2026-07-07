"""L3: кросс-секционная факторная модель — «как факторы складываются» по вселенной акций.

Превращает сырые фундаментальные метрики (`asset_fundamentals`) в стандартизованные по вселенной
факторные экспозиции: value (дёшево), quality (качество бизнеса), growth (рост) и композит.
Каждая суб-метрика z-нормируется по вселенной (винзор ±3), категория — среднее доступных z,
композит — среднее категорий. `percentile` — ранг актива внутри вселенной [0..100].

Чистое ядро `cross_sectional_scores` (без БД) — основной предмет тестов; DB-раннер
`record_factor_scores` собирает входы из БД, считает и идемпотентно пишет дневной срез в
`factor_scores` (накопление во времени → тренд). Эталон — Aladdin-подобный факторный синтез.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from geoanalytics.analytics.fundamental_factors import _SANE_PB, _SANE_PE, latest_metrics
from geoanalytics.core.logging import get_logger
from geoanalytics.storage.models import Asset, AssetFundamental
from geoanalytics.storage.repositories import FactorScoreRepository

log = get_logger("analytics.factor_model")

# Суб-метрики по категориям. Конвенция: ВЫШЕ = ЛУЧШЕ (value: дешевле/щедрее; quality: сильнее
# бизнес; growth: быстрее рост). Дорогие/слабые/падающие получают низкий z.
_VALUE_SUBS = ("earnings_yield", "book_yield", "div_yield")     # 1/PE, 1/PB, див.доходность
_QUALITY_SUBS = ("roe", "net_margin", "neg_leverage")           # ROE, маржа, −ND/EBITDA
_GROWTH_SUBS = ("rev_growth", "profit_growth")                  # YoY выручки/прибыли, %
_FACTORS: dict[str, tuple[str, ...]] = {
    "value": _VALUE_SUBS, "quality": _QUALITY_SUBS, "growth": _GROWTH_SUBS,
}
_ALL_SUBS = _VALUE_SUBS + _QUALITY_SUBS + _GROWTH_SUBS
_Z_CLAMP = 3.0

_FACTOR_LABELS = {
    "value": "Value (дёшево)", "quality": "Quality (качество)",
    "growth": "Growth (рост)", "composite": "Композит",
}


# --------------------------------------------------------------------------- #
# Чистое ядро (тестируется без БД)
# --------------------------------------------------------------------------- #
def _zscores(values: dict[int, float]) -> dict[int, float]:
    """{asset_id: raw} → {asset_id: z}, винзор ±3. <2 точек или нулевой разброс → все 0."""
    xs = list(values.values())
    if len(xs) < 2:
        return dict.fromkeys(values, 0.0)
    mu = statistics.fmean(xs)
    sd = statistics.pstdev(xs)
    if sd == 0:
        return dict.fromkeys(values, 0.0)
    return {k: max(-_Z_CLAMP, min(_Z_CLAMP, (v - mu) / sd)) for k, v in values.items()}


def _percentiles(values: dict[int, float]) -> dict[int, float]:
    """{asset_id: score} → {asset_id: перцентиль [0..100]} по возрастанию (выше score → выше)."""
    if not values:
        return {}
    ranked = sorted(values.items(), key=lambda kv: kv[1])
    n = len(ranked)
    if n == 1:
        return {ranked[0][0]: 50.0}
    return {aid: round(100 * i / (n - 1), 1) for i, (aid, _) in enumerate(ranked)}


def cross_sectional_scores(inputs: dict[int, dict[str, float]]) -> dict[int, dict[str, dict]]:
    """Сырые суб-метрики по вселенной → факторные скоры. Чистая функция (ядро тестов).

    `inputs`: ``{asset_id: {submetric: value}}`` (только присутствующие суб-метрики).
    Возврат: ``{asset_id: {factor: {"zscore", "percentile"}}}`` для
    factor ∈ value/quality/growth/composite (только где есть данные)."""
    # 1) z по каждой суб-метрике отдельно (по присутствующим активам).
    sub_z: dict[str, dict[int, float]] = {}
    for sub in _ALL_SUBS:
        present = {aid: d[sub] for aid, d in inputs.items() if d.get(sub) is not None}
        if present:
            sub_z[sub] = _zscores(present)

    # 2) категория = среднее доступных z суб-метрик; композит = среднее категорий.
    cat_raw: dict[str, dict[int, float]] = {
        "value": {}, "quality": {}, "growth": {}, "composite": {},
    }
    for aid in inputs:
        cat_vals: dict[str, float] = {}
        for factor, subs in _FACTORS.items():
            zs = [sub_z[s][aid] for s in subs if s in sub_z and aid in sub_z[s]]
            if zs:
                cat_vals[factor] = statistics.fmean(zs)
        for factor, val in cat_vals.items():
            cat_raw[factor][aid] = val
        if cat_vals:
            cat_raw["composite"][aid] = statistics.fmean(list(cat_vals.values()))

    # 3) перцентили внутри каждого фактора + сборка результата.
    out: dict[int, dict[str, dict]] = {aid: {} for aid in inputs}
    for factor, vals in cat_raw.items():
        pct = _percentiles(vals)
        for aid, z in vals.items():
            out[aid][factor] = {"zscore": round(z, 3), "percentile": pct[aid]}
    return {aid: f for aid, f in out.items() if f}


# --------------------------------------------------------------------------- #
# Сбор входов из БД
# --------------------------------------------------------------------------- #
def _metric_growth(session, asset_id: int, metric: str) -> float | None:
    """YoY рост (%) метрики между двумя свежайшими ГОДОВЫМИ периодами. None — <2 лет / база ≤0."""
    rows = session.scalars(
        select(AssetFundamental).where(
            AssetFundamental.asset_id == asset_id,
            AssetFundamental.metric == metric,
        ).order_by(AssetFundamental.period)
    ).all()
    by_year = {r.period: r.value for r in rows if r.period and r.period.isdigit()}
    years = sorted(by_year)
    if len(years) < 2:
        return None
    prev, last = by_year[years[-2]], by_year[years[-1]]
    if prev is None or prev <= 0 or last is None:
        return None
    return (last - prev) / prev * 100


def gather_inputs(session, asset_ids: list[int]) -> dict[int, dict[str, float]]:
    """Сырые суб-метрики по активам из `asset_fundamentals` (только где есть фундаментал)."""
    inputs: dict[int, dict[str, float]] = {}
    for aid in asset_ids:
        m = latest_metrics(session, aid)
        if not m:
            continue
        d: dict[str, float] = {}
        pe, pb = m.get("pe"), m.get("pb")
        if pe and _SANE_PE[0] < pe <= _SANE_PE[1]:
            d["earnings_yield"] = 1.0 / pe
        if pb and _SANE_PB[0] < pb <= _SANE_PB[1]:
            d["book_yield"] = 1.0 / pb
        if m.get("div_yield") is not None:
            d["div_yield"] = m["div_yield"]
        if m.get("roe") is not None:
            d["roe"] = m["roe"]
        if m.get("net_margin") is not None:
            d["net_margin"] = m["net_margin"]
        nd, eb = m.get("net_debt"), m.get("ebitda")
        if nd is not None and eb and eb > 0:
            d["neg_leverage"] = -(nd / eb)
        rg = _metric_growth(session, aid, "revenue")
        if rg is not None:
            d["rev_growth"] = rg
        pg = _metric_growth(session, aid, "net_profit")
        if pg is not None:
            d["profit_growth"] = pg
        if d:
            inputs[aid] = d
    return inputs


def _share_universe(session) -> list[int]:
    """ID торгуемых акций с привязанной компанией (вселенная для кросс-секции)."""
    return list(session.scalars(
        select(Asset.id).where(Asset.kind == "share", Asset.company_id.isnot(None))
    ))


# --------------------------------------------------------------------------- #
# DB-раннер и читатель
# --------------------------------------------------------------------------- #
def record_factor_scores(session, day=None) -> int:
    """Посчитать кросс-секционные факторы по вселенной и идемпотентно записать срез на день.

    Возвращает число записанных строк (актив×фактор). `day` по умолчанию — сегодня (UTC)."""
    day = day or datetime.now(UTC).date()
    universe = _share_universe(session)
    inputs = gather_inputs(session, universe)
    scores = cross_sectional_scores(inputs)
    repo = FactorScoreRepository(session)
    repo.delete_day(day)
    n = 0
    for aid, factors in scores.items():
        for factor, sc in factors.items():
            repo.add(day, aid, factor, sc["zscore"], sc["percentile"])
            n += 1
    log.info("factor_scores_done", day=str(day), assets=len(scores), rows=n)
    return n


def backfill_scores(session, days: int = 5) -> int:
    """Идемпотентно дозаписать факторные срезы за пропущенные дни окна (самозалечивание).

    Факторы квазистатичны внутри недели (фундаменталка квартальная), поэтому пропущенные
    из-за простоя дни заполняем ТЕКУЩИМ срезом — ради непрерывности тренда. Уже записанные
    дни и выходные пропускаем. Возвращает суммарно записанные строки."""
    today = datetime.now(UTC).date()
    existing = FactorScoreRepository(session).recorded_days(today - timedelta(days=days))
    total = 0
    for i in range(days, -1, -1):
        d = today - timedelta(days=i)
        if d.isoweekday() > 5 or d in existing:
            continue
        total += record_factor_scores(session, d)
    return total


def factor_scores_for_asset(session, asset_id: int) -> list[dict]:
    """Свежие факторные экспозиции актива для карточки в порядке value→quality→growth→композит.

    ``[{factor, label, zscore, percentile, day}]``."""
    rows = FactorScoreRepository(session).latest_for_asset(asset_id)
    out: list[dict] = []
    for factor in ("value", "quality", "growth", "composite"):
        r = rows.get(factor)
        if r is None:
            continue
        out.append({"factor": factor, "label": _FACTOR_LABELS[factor],
                    "zscore": r.zscore, "percentile": r.percentile, "day": r.day})
    return out
