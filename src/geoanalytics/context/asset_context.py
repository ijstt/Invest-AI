"""Построение и хранение контекста актива.

Агрегирует разнородные сигналы в единый «контекст»:
- технические индикаторы (analytics);
- макро-оверлей (ставка, курсы);
- новостной фон (количество, тональность, темы) по связанным новостям;
- факторы влияния (сектор, пиры, макро-драйверы) из графа.

Результат сохраняется версионно в таблицу asset_context: drivers (структурно) +
narrative (связный текст; LLM при доступности, иначе шаблон).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from geoanalytics.analytics.correlations import correlate_asset
from geoanalytics.analytics.macro import macro_snapshot
from geoanalytics.analytics.prices import asset_indicators
from geoanalytics.context.events import top_impacts_for_asset
from geoanalytics.context.graph import factors_for_asset
from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import Sentiment
from geoanalytics.nlp import llm
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import Article, ArticleEntity, Asset, AssetContext

log = get_logger("context")


@dataclass
class NewsBackground:
    recent_count: int = 0
    sentiment: dict[str, int] = field(default_factory=dict)
    top_events: list[tuple[str, int]] = field(default_factory=list)
    score: float = 0.0  # средневзвешенный sentiment_score связанных новостей


def _news_background(session: Session, asset_id: int | list[int], hours: int = 168,
                     entity_type: str = "asset") -> NewsBackground:
    """Новостной фон по активу/списку активов за окно (по умолчанию неделя).

    `asset_id` может быть int или списком id (агрегат по сектору) — один IN-запрос.
    `entity_type` — тип связи ArticleEntity (asset/sector/country).
    """
    from datetime import datetime, timedelta

    ids = [asset_id] if isinstance(asset_id, int) else list(asset_id)
    since = datetime.now(UTC) - timedelta(hours=hours)
    if not ids:
        return NewsBackground()
    articles = list(session.scalars(
        select(Article)
        .join(ArticleEntity, ArticleEntity.article_id == Article.id)
        .where(
            ArticleEntity.entity_type == entity_type,
            ArticleEntity.entity_id.in_(ids),
            Article.published_at >= since,
        )
        .order_by(desc(Article.published_at))
        .distinct()
    ))
    bg = NewsBackground(recent_count=len(articles))
    sent_counter: Counter = Counter()
    event_counter: Counter = Counter()
    scores = []
    for a in articles:
        sent_counter[a.sentiment or Sentiment.NEUTRAL.value] += 1
        if a.event_type:
            event_counter[a.event_type] += 1
        if a.sentiment_score is not None:
            scores.append(a.sentiment_score)
    bg.sentiment = dict(sent_counter)
    bg.top_events = event_counter.most_common(5)
    bg.score = round(sum(scores) / len(scores), 3) if scores else 0.0
    return bg


def _template_narrative(ticker: str, drivers: dict) -> str:
    """Шаблонный нарратив на случай недоступности LLM."""
    t = drivers.get("technical", {})
    bg = drivers.get("news", {})
    parts = [f"{ticker}:"]
    if t.get("trend"):
        parts.append(f"тренд {t['trend']}")
    if t.get("rsi14") is not None:
        zone = ("перекупленность" if t["rsi14"] > 70
                else "перепроданность" if t["rsi14"] < 30 else "нейтрально")
        parts.append(f"RSI {t['rsi14']} ({zone})")
    if t.get("ret_1m") is not None:
        parts.append(f"за месяц {t['ret_1m']:+}%")
    sent = bg.get("sentiment", {})
    if bg.get("recent_count"):
        parts.append(
            f"новостной фон за неделю: {bg['recent_count']} публикаций "
            f"(+{sent.get('positive', 0)}/-{sent.get('negative', 0)})"
        )
    macro = drivers.get("macro", {})
    if macro.get("key_rate") is not None:
        parts.append(f"ключевая ставка {macro['key_rate']}%")
    return ", ".join(parts) + "."


def _sentiment_trend_driver(session: Session, asset_id: int, technical: dict) -> dict:
    """Тональный тренд (B1) для grounding: EWMA-моментум, ширина, дивергенция с ценой.

    Берёт последнюю сохранённую строку индекса настроения по активу; дивергенцию считает по
    знаку дневной доходности (ret_1w как ближайший доступный) против знака тонального моментума.
    Пустой dict, если индекс ещё не материализован (секция грунта тогда опускается).
    """
    from geoanalytics.analytics.market_sentiment import is_divergent, latest

    row = latest(session, "asset", asset_id=asset_id)
    if row is None:
        return {}
    price_chg = technical.get("ret_1w")
    return {"ewma": round(row.sent_ewma, 3), "breadth": round(row.breadth, 3),
            "dispersion": round(row.dispersion, 3), "price_change_pct": price_chg,
            "diverging": is_divergent(price_chg, row.sent_ewma)}


def _llm_narrative(ticker: str, name: str, drivers: dict) -> str | None:
    if not llm.is_available():
        return None
    # Грунт интерпретированным РУССКИМ текстом (а не сырым JSON): лёгкие модели путаются в
    # числах dict — `render_grounding` даёт зоны/тренды/настроение словами ([[grounding]]).
    from geoanalytics.context.grounding import render_grounding

    grounding = render_grounding(drivers, header=f"ОБЪЕКТ: {name} ({ticker}), рынок РФ.")
    prompt = (
        f"Ты финансовый аналитик. Кратко (4–6 предложений) опиши текущий контекст "
        f"актива {name} ({ticker}) для инвестора: техническое состояние, новостной фон, "
        f"влияние макро и ключевые факторы. Только по делу, без воды.\n\n{grounding}"
    )
    return llm.generate(prompt)


def build_context(ticker: str, use_llm: bool = True) -> dict | None:
    """Строит и сохраняет новую версию контекста актива. Возвращает drivers+narrative."""
    with session_scope() as session:
        asset = session.scalars(select(Asset).where(Asset.ticker == ticker.upper())).first()
        if asset is None:
            return None

        ind = asset_indicators(session, asset.id)
        macro = macro_snapshot(session)
        bg = _news_background(session, asset.id)
        factors = factors_for_asset(session, asset)
        correlations = correlate_asset(session, asset)
        impacts = top_impacts_for_asset(session, asset.id)
        sentiment_trend = _sentiment_trend_driver(session, asset.id, ind.as_dict())

        drivers = {
            "technical": ind.as_dict(),
            "sentiment_trend": sentiment_trend,
            "macro": macro.as_dict(),
            "news": {
                "recent_count": bg.recent_count,
                "sentiment": bg.sentiment,
                "top_events": bg.top_events,
                "score": bg.score,
            },
            "factors": factors.as_dict(),
            "correlations": correlations,
            "impacting_events": impacts,
        }

        narrative = (_llm_narrative(asset.ticker, asset.name, drivers) if use_llm else None) \
            or _template_narrative(asset.ticker, drivers)

        # Версионирование: следующая версия = max+1.
        last_ver = session.scalars(
            select(AssetContext.version).where(AssetContext.asset_id == asset.id)
            .order_by(desc(AssetContext.version)).limit(1)
        ).first() or 0
        session.add(AssetContext(asset_id=asset.id, version=last_ver + 1,
                                 narrative=narrative, drivers=drivers))
        log.info("context_built", ticker=asset.ticker, version=last_ver + 1)
        return {"drivers": drivers, "narrative": narrative, "version": last_ver + 1}


def latest_context(session: Session, asset_id: int) -> AssetContext | None:
    """Последняя сохранённая версия контекста актива."""
    return session.scalars(
        select(AssetContext).where(AssetContext.asset_id == asset_id)
        .order_by(desc(AssetContext.version)).limit(1)
    ).first()
