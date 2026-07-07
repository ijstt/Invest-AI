"""Сборка сводки «что по новостям».

Источники данных сводки:
- макро (ключевая ставка, курсы) — из нормализованных таблиц MacroSeries/FxRate;
- топ-движения рынка — из последнего живого среза MOEX в raw-слое (change_pct);
- новости, тональность, темы — из обогащённой таблицы articles (после processing);
- опциональная связная сводка — LLM (Ollama/cloud), при недоступности деградирует
  до списка заголовков.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from math import exp

from sqlalchemy import desc, func, select

from geoanalytics.core.types import EntityType, Sentiment
from geoanalytics.nlp import llm
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import (
    Article,
    ArticleEntity,
    Asset,
    FxRate,
    MacroSeries,
    RawDocument,
)

# Мягкий приоритет типов событий для сводки рынка.
# Прямые корпоративные/санкционные драйверы > макро/регуляторика > геополитика (влияет
# косвенно, через настроения) > прочее > шум. Используется в _rank_score() как нормированный
# весовой коэффициент 0..1, а не как ступенчатый tie-break — геополитика sig=0.85 обгоняет
# макро sig=0.5, но уступает макро sig=0.85.
_MARKET_PRIORITY = {
    "earnings": 5, "dividends": 5, "merger": 5, "sanctions": 5,
    "macro": 4, "regulation": 4,
    "geopolitics": 2,
    "other": 1, "noise": 0,
}

# Нормированные веса: делим на максимальный приоритет (5) → диапазон [0, 1].
_TYPE_WEIGHT: dict[str, float] = {k: v / 5.0 for k, v in _MARKET_PRIORITY.items()}

# Период полураспада свежести в сутках: через 1.5д вес свежести снижается вдвое.
_FRESHNESS_HALF_DAYS: float = 1.5
_LN2: float = 0.6931471805599453


def _rank_score(sig: float, event_type: str | None, published_at: datetime,
                reliability: float = 1.0, factuality: str | None = None) -> float:
    """Непрерывный скор для ранжирования новостей в сводке рынка.

    Combines significance × type relevance × freshness decay × достоверность (F7).
    Формула: sig * (0.4 + 0.6 * type_w) * exp(-age_days * ln2 / half_life) * cred
    - type_w ∈ [0, 1] смягчает влияние типа (не обнуляет геополитику при sig=0.85)
    - freshness decay даёт 50% скидку через 1.5 суток
    - cred ∈ [0.5, 1.0]: надёжность источника + штраф за слух/мнение (F4/F7) —
      болтливые/спекулятивные каналы тонут (лечит «Трамп 🗣» sig=0.85)
    """
    from geoanalytics.analytics.source_reliability import credibility_multiplier

    type_w = _TYPE_WEIGHT.get(event_type or "other", 0.2)
    age_days = (datetime.now(UTC) - published_at).total_seconds() / 86400.0
    freshness = exp(-age_days * _LN2 / _FRESHNESS_HALF_DAYS)
    cred = credibility_multiplier(reliability, factuality)
    return sig * (0.4 + 0.6 * type_w) * freshness * cred


@dataclass
class MarketSnapshot:
    key_rate: float | None = None
    key_rate_date: str | None = None
    fx: dict[str, float] = field(default_factory=dict)
    top_gainers: list[dict] = field(default_factory=list)
    top_losers: list[dict] = field(default_factory=list)
    # {title, sentiment, event_type, url, published_at, significance, tickers}
    headlines: list[dict] = field(default_factory=list)
    sentiment_breakdown: dict[str, int] = field(default_factory=dict)
    top_events: list[tuple[str, int]] = field(default_factory=list)
    # B1-консенсус: агрегированная стойка рынка из индекса настроения во времени.
    # {stance, sent_ewma, breadth, dispersion, n_docs, sectors_pos, sectors_neg, divergences}
    consensus: dict = field(default_factory=dict)
    llm_summary: str | None = None


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _macro(session, snap: MarketSnapshot) -> None:
    """Ключевая ставка и последние курсы валют из нормализованных таблиц."""
    rate = session.scalars(
        select(MacroSeries).where(MacroSeries.indicator == "key_rate")
        .order_by(desc(MacroSeries.ts)).limit(1)
    ).first()
    if rate is not None:
        snap.key_rate = float(rate.value)
        snap.key_rate_date = rate.ts.strftime("%d.%m.%Y")

    # Последний курс по каждой валюте (max ts на валюту).
    latest_ts = (
        select(FxRate.currency, func.max(FxRate.ts).label("ts"))
        .group_by(FxRate.currency).subquery()
    )
    rows = session.scalars(
        select(FxRate).join(
            latest_ts,
            (FxRate.currency == latest_ts.c.currency) & (FxRate.ts == latest_ts.c.ts),
        )
    )
    for fx in rows:
        snap.fx[fx.currency] = float(fx.value)


def _movers(session, snap: MarketSnapshot, top_n: int) -> None:
    """Топ-движения из последнего живого среза MOEX (raw payload change_pct)."""
    stmt = (
        select(RawDocument.payload)
        .where(RawDocument.source == "moex", RawDocument.payload.isnot(None))
        .order_by(RawDocument.fetched_at.desc())
        .limit(2000)
    )
    moves, seen = [], set()
    for (p,) in session.execute(stmt):
        ticker, chg = p.get("ticker"), _to_float(p.get("change_pct"))
        if not ticker or ticker in seen or chg is None:
            continue
        seen.add(ticker)
        moves.append({"ticker": ticker, "name": p.get("name"),
                      "change_pct": chg, "last": _to_float(p.get("last"))})
    moves.sort(key=lambda m: m["change_pct"], reverse=True)
    snap.top_gainers = moves[:top_n]
    snap.top_losers = list(reversed(moves[-top_n:])) if len(moves) > top_n else []


def _news(session, snap: MarketSnapshot, hours: int, headline_n: int,
          by_significance: bool = False) -> list[Article]:
    """Свежие обогащённые новости + распределение тональности и тем.

    Распределение тональности/тем считается по всему свежему окну. Выбор показываемых
    заголовков управляется `by_significance`: для СВОДКИ рынка (True) берём самые
    значимые новости окна (дистиллированная Ф7 significance — мусор вроде графика
    работы соцучреждений/Roblox имеет sig≈0.15 и не всплывает), для живой ленты
    (False) — просто самые свежие в хронологическом порядке.
    """
    since = datetime.now(UTC) - timedelta(hours=hours)
    # F10: прогнозы брокеров (is_forecast) НЕ льём в сводку/сентимент рынка как факт —
    # «брокеры торгуют книгу»; их ожидания живут отдельно в forecasts.
    articles = list(session.scalars(
        select(Article)
        .where(Article.published_at >= since, Article.is_forecast.is_(False))
        .order_by(desc(Article.published_at)).limit(headline_n * 5)
    ))
    sent_counter: Counter = Counter()
    event_counter: Counter = Counter()
    for a in articles:
        sent_counter[a.sentiment or Sentiment.NEUTRAL.value] += 1
        if a.event_type:
            event_counter[a.event_type] += 1
    snap.sentiment_breakdown = dict(sent_counter)
    snap.top_events = event_counter.most_common(5)

    if by_significance:
        # Сводка рынка: непрерывный rank_score = sig × тип × свежесть × достоверность.
        # Геополитика sig=0.85 обгоняет макро sig=0.5, но уступает макро sig=0.85;
        # надёжность источника (F7) и фактологичность (F4) дополнительно опускают
        # болтливые/спекулятивные каналы.
        from geoanalytics.analytics.source_reliability import (
            reliability_lookup,
            trust_prior,
        )
        rel_map = reliability_lookup(session)

        def _rel(a: Article) -> float:
            key = a.source_ref or a.source
            return rel_map.get(key, trust_prior(key))

        articles = sorted(
            articles,
            key=lambda a: _rank_score(
                a.significance or 0.0, a.event_type, a.published_at,
                _rel(a), a.factuality,
            ),
            reverse=True)
    head = articles[:headline_n]
    # Тикеры связанных активов для показываемых заголовков (один батч-запрос).
    tickers: dict[int, list[str]] = defaultdict(list)
    ids = [a.id for a in head]
    if ids:
        rows = session.execute(
            select(ArticleEntity.article_id, Asset.ticker)
            .join(Asset, Asset.id == ArticleEntity.entity_id)
            .where(ArticleEntity.entity_type == EntityType.ASSET.value,
                   ArticleEntity.article_id.in_(ids))
        )
        for aid, tk in rows:
            tickers[aid].append(tk)
    for a in head:
        snap.headlines.append({
            "title": a.title, "sentiment": a.sentiment, "event_type": a.event_type,
            "url": a.url, "published_at": a.published_at, "significance": a.significance,
            "tickers": tickers.get(a.id, []),
        })
    return head


def _stance(ewma: float, breadth: float) -> str:
    """Стойка рынка по тональному моментуму и ширине настроения."""
    if ewma > 0.1 and breadth > 0:
        return "позитивная"
    if ewma < -0.1 and breadth < 0:
        return "негативная"
    if abs(ewma) <= 0.1:
        return "нейтральная"
    return "смешанная"


def _consensus(session, snap: MarketSnapshot) -> None:
    """B1-консенсус: стойка рынка (EWMA/breadth) + перекос секторов + дивергенции топ-движений.

    Использует материализованный индекс настроения (`market_sentiment`). Если индекс ещё пуст —
    consensus остаётся пустым (сводка деградирует к прежнему виду без стойки).
    """
    from geoanalytics.analytics.market_sentiment import is_divergent, latest
    from geoanalytics.storage.models import MarketSentiment

    mkt = latest(session, "market")
    if mkt is None:
        return
    snap.consensus = {
        "stance": _stance(mkt.sent_ewma, mkt.breadth),
        "sent_ewma": round(mkt.sent_ewma, 3), "breadth": round(mkt.breadth, 2),
        "dispersion": round(mkt.dispersion, 2), "n_docs": mkt.n_docs,
        "day": mkt.day.isoformat(),
    }
    # Перекос секторов на последний день индекса (топ-3 позитив/негатив по EWMA).
    sec_rows = list(session.scalars(
        select(MarketSentiment).where(MarketSentiment.scope == "sector",
                                      MarketSentiment.day == mkt.day)
        .order_by(MarketSentiment.sent_ewma.desc())))
    snap.consensus["sectors_pos"] = [(r.sector, round(r.sent_ewma, 2))
                                     for r in sec_rows if r.sent_ewma > 0.05][:3]
    snap.consensus["sectors_neg"] = [(r.sector, round(r.sent_ewma, 2))
                                     for r in reversed(sec_rows) if r.sent_ewma < -0.05][:3]
    # Дивергенции: топ-движения, чьё направление цены расходится с настроением актива.
    divergences = []
    asset_id_by_ticker = dict(session.execute(
        select(Asset.ticker, Asset.id).where(
            Asset.ticker.in_([m["ticker"] for m in snap.top_gainers + snap.top_losers]))).all())
    for m in snap.top_gainers + snap.top_losers:
        aid = asset_id_by_ticker.get(m["ticker"])
        if aid is None:
            continue
        srow = latest(session, "asset", asset_id=aid)
        if srow and is_divergent(m["change_pct"], srow.sent_ewma):
            divergences.append({"ticker": m["ticker"], "change_pct": m["change_pct"],
                                "sent_ewma": round(srow.sent_ewma, 2)})
    snap.consensus["divergences"] = divergences[:5]


def _llm_summary(snap: MarketSnapshot, articles: list[Article]) -> None:
    """Связная сводка через LLM. None, если LLM недоступен."""
    if not articles or not llm.is_available():
        return
    headlines = "\n".join(f"- {a.title}" for a in articles)
    macro = []
    if snap.key_rate is not None:
        macro.append(f"ключевая ставка {snap.key_rate}%")
    macro += [f"{c} {v:.2f}₽" for c, v in snap.fx.items()]
    cons = ""
    if snap.consensus:
        c = snap.consensus
        cons = (f"\n\nКонсенсус настроения (индекс): стойка {c['stance']}, "
                f"моментум {c['sent_ewma']:+}, ширина {c['breadth']:+} (n={c['n_docs']}).")
        if c.get("divergences"):
            d = ", ".join(f"{x['ticker']} (цена {x['change_pct']:+}%, настроение "
                          f"{x['sent_ewma']:+})" for x in c["divergences"][:3])
            cons += f" Дивергенции цена↔настроение: {d}."
    prompt = (
        "Ты финансовый аналитик. На основе заголовков новостей, макроданных и консенсуса "
        "настроения дай краткую (3–5 предложений) сводку текущей ситуации для российского "
        "рынка. Без воды, по делу.\n\n"
        f"Макро: {', '.join(macro) or 'нет данных'}.{cons}\n\nЗаголовки:\n{headlines}"
    )
    snap.llm_summary = llm.generate(prompt)


def build_snapshot(top_n: int = 5, headline_n: int = 10, hours: int = 24,
                   use_llm: bool = True) -> MarketSnapshot:
    """Строит снимок рынка из нормализованных данных и обогащённых новостей."""
    snap = MarketSnapshot()
    with session_scope() as session:
        _macro(session, snap)
        _movers(session, snap, top_n)
        articles = _news(session, snap, hours, headline_n, by_significance=True)
        _consensus(session, snap)        # B1: стойка рынка + дивергенции (после _movers)
    if use_llm:
        _llm_summary(snap, articles)
    return snap


def recent_headlines(hours: int = 24, limit: int = 15) -> list[dict]:
    """Лента свежих заголовков (для авто-обновляемого блока дашборда без LLM/движений)."""
    snap = MarketSnapshot()
    with session_scope() as session:
        _news(session, snap, hours, limit)
    return snap.headlines
