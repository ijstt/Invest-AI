"""L3 (долгосрок): фундаментальные суждения — квалити-скрин и fair value.

Превращает сырые метрики (`asset_fundamentals`, наполняется connectors/smartlab) в оценки:
- ``quality_screen`` — чистая оценка качества бизнеса [0..1] + вердикт ok/caution/avoid +
  флаги (убыток, отрицательный FCF, высокий долг ND/EBITDA, низкий/отриц. ROE, payout>100%,
  падающая маржа). Это ядро запроса «понимать, когда компания плоха, чтобы вовсе не входить».
- ``fair_value`` — ОТНОСИТЕЛЬНАЯ оценка к сектору по медианным мультипликаторам (P/E, P/B):
  апсайд/даунсайд. Грубая (не DCF) — относительная недо/переоценка, не цель по цене.

`quality_screen` — чистая (тестируется без БД); `fair_value`/`fundamental_inputs` тянут БД.
Драйвер для стойки (`fundamental_driver`) живёт в recommendation.py (там dataclass Driver),
чтобы не плодить циклический импорт.
"""

from __future__ import annotations

from datetime import date
from statistics import median

from sqlalchemy import select

from geoanalytics.storage.models import Asset, AssetFundamental

_MIN_PEERS = 3                # минимум активов сектора с мультипликатором для медианы
# Вменяемые диапазоны мультипликаторов — отсекают искажения от предварительных/частичных
# данных за последний год (напр. P/E из почти нулевой прибыли → сотни).
_SANE_PE = (0.0, 40.0)
_SANE_PB = (0.0, 15.0)
_FV_CLAMP = 50.0              # винзоризация относительного апсайда/даунсайда, %


def quality_screen(m: dict[str, float], margin_trend: float | None = None) -> dict:
    """Качество бизнеса из метрик → {score[0..1], verdict, flags, positives}. Чистая.

    `m` — метрики в наших единицах (RUB для денежных, проценты для pct, коэффициент для ratio).
    `margin_trend` — изменение чистой маржи (п.п.) за доступный период (<0 — ухудшение).
    Стартует с нейтральных 0.5, плюсы/минусы сдвигают; missing-метрики просто пропускаются
    (банки без EBITDA/FCF оцениваются по ROE/payout — деградация мягкая)."""
    score = 0.5
    flags: list[str] = []
    positives: list[str] = []

    np_ = m.get("net_profit")
    if np_ is not None and np_ < 0:
        score -= 0.30
        flags.append("убыток")

    fcf = m.get("fcf")
    if fcf is not None and fcf < 0:
        score -= 0.15
        flags.append("отрицательный FCF")

    nd, eb = m.get("net_debt"), m.get("ebitda")
    if nd is not None and eb and eb > 0:
        if nd < 0:
            score += 0.12
            positives.append("чистая денежная позиция")
        else:
            lev = nd / eb
            if lev > 4:
                score -= 0.25
                flags.append(f"высокий долг ND/EBITDA {lev:.1f}")
            elif lev > 3:
                score -= 0.12
                flags.append(f"повышенный долг ND/EBITDA {lev:.1f}")
            elif lev < 1.5:
                score += 0.08
                positives.append(f"низкий долг ND/EBITDA {lev:.1f}")

    roe = m.get("roe")
    if roe is not None:
        if roe >= 20:
            score += 0.18
            positives.append(f"ROE {roe:.0f}%")
        elif roe >= 12:
            score += 0.08
        elif roe < 0:
            score -= 0.20
            flags.append("отрицательный ROE")
        elif roe < 5:
            score -= 0.05

    nm = m.get("net_margin")
    if nm is not None:
        if nm >= 20:
            score += 0.08
            positives.append(f"маржа {nm:.0f}%")
        elif nm < 0:
            score -= 0.12
            flags.append("отрицательная маржа")

    payout = m.get("payout")
    if payout is not None and 100 < payout <= 300:    # >300% — почти всегда артефакт данных
        score -= 0.10
        flags.append(f"payout {payout:.0f}%")

    if margin_trend is not None and margin_trend < -2.0:
        score -= 0.08
        flags.append("падающая маржа")

    score = max(0.0, min(1.0, score))
    # "ok" требует положительных свидетельств (>0.55); нейтраль/нет данных → "caution".
    verdict = "avoid" if score < 0.35 else "caution" if score < 0.55 else "ok"
    return {"score": round(score, 3), "verdict": verdict, "flags": flags,
            "positives": positives}


def latest_metrics(session, asset_id: int) -> dict[str, float]:
    """Свежайшая метрика на ключ → {metric: value} (через AssetFundamentalRepository)."""
    from geoanalytics.storage.repositories import AssetFundamentalRepository

    rows = AssetFundamentalRepository(session).latest_for_asset(asset_id)
    return {r.metric: r.value for r in rows}


def _margin_trend(session, asset_id: int) -> float | None:
    """Изменение чистой маржи (п.п.) от самого раннего к свежему годовому периоду."""
    rows = session.scalars(
        select(AssetFundamental).where(
            AssetFundamental.asset_id == asset_id,
            AssetFundamental.metric == "net_margin",
        ).order_by(AssetFundamental.period)
    ).all()
    vals = [r.value for r in rows if r.period and r.period.isdigit()]
    return (vals[-1] - vals[0]) if len(vals) >= 2 else None


def _last_full_year_value(session, asset_id: int, metric: str,
                          current_year: int | None = None) -> float | None:
    """Значение метрики за последний ПОЛНЫЙ фин.год (period == 'YYYY'), исключая текущий
    календарный год.

    Текущий год заведомо неполон — предварительные цифры (почти нулевая годовая прибыль и т.п.)
    искажают мультипликаторы P/E, P/B. Берём последний завершённый год; фолбэк — свежайший
    годовой период, если завершённых ещё нет."""
    current_year = current_year or date.today().year
    rows = session.execute(
        select(AssetFundamental.period, AssetFundamental.value).where(
            AssetFundamental.asset_id == asset_id,
            AssetFundamental.metric == metric,
        )
    ).all()
    annual = [(int(p), v) for p, v in rows if p and p.isdigit()]
    if not annual:
        return None
    complete = [(y, v) for y, v in annual if y < current_year]
    pool = complete or annual
    pool.sort(reverse=True)
    return pool[0][1]


def fair_value(session, asset_id: int) -> dict | None:
    """Относительная оценка к сектору по медианным P/E и P/B → апсайд/даунсайд (%).

    Грубая относительная недо/переоценка (не DCF): сравнивает мультипликаторы актива с
    медианой сектора. Мультипликаторы берутся за последний ПОЛНЫЙ фин.год (`_last_full_year_value`)
    — не за текущий неполный/предварительный. None — нет сектора/недостаточно сопоставимых пиров."""
    asset = session.get(Asset, asset_id)
    if asset is None or asset.company is None or asset.company.sector_id is None:
        return None
    from geoanalytics.context.graph import assets_in_sector

    peers = assets_in_sector(session, asset.company.sector_id)   # включая сам актив
    pes: list[float] = []
    pbs: list[float] = []
    for a in peers:
        pe = _last_full_year_value(session, a.id, "pe")
        pb = _last_full_year_value(session, a.id, "pb")
        if pe and _SANE_PE[0] < pe <= _SANE_PE[1]:
            pes.append(pe)
        if pb and _SANE_PB[0] < pb <= _SANE_PB[1]:
            pbs.append(pb)
    med_pe = median(pes) if len(pes) >= _MIN_PEERS else None
    med_pb = median(pbs) if len(pbs) >= _MIN_PEERS else None

    a_pe = _last_full_year_value(session, asset_id, "pe")
    a_pb = _last_full_year_value(session, asset_id, "pb")
    ups: list[float] = []
    if med_pe and a_pe and _SANE_PE[0] < a_pe <= _SANE_PE[1]:
        ups.append(med_pe / a_pe - 1.0)
    if med_pb and a_pb and _SANE_PB[0] < a_pb <= _SANE_PB[1]:
        ups.append(med_pb / a_pb - 1.0)
    if not ups:
        return None
    upside = max(-_FV_CLAMP, min(_FV_CLAMP, sum(ups) / len(ups) * 100))
    verdict = ("недооценён" if upside > 15 else "переоценён" if upside < -15
               else "справедливо")
    return {
        "upside_pct": round(upside, 1), "verdict": verdict,
        "sector_median_pe": round(med_pe, 1) if med_pe else None,
        "sector_median_pb": round(med_pb, 2) if med_pb else None,
        "asset_pe": a_pe, "asset_pb": a_pb, "n_peers": len(pes),
    }


def fundamental_inputs(session, asset_id: int) -> dict | None:
    """Сводка фундаментала актива для стойки/карточки: {metrics, quality, fair_value}.

    None — фундаментала нет (индексы/фьючерсы/несоскрейпленные)."""
    metrics = latest_metrics(session, asset_id)
    if not metrics:
        return None
    quality = quality_screen(metrics, margin_trend=_margin_trend(session, asset_id))
    return {"metrics": metrics, "quality": quality, "fair_value": fair_value(session, asset_id)}
