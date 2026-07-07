"""Извлечение событий из новостей и оценка их влияния на активы.

Из обогащённых статей (значимый event_type + связанные активы) формируются:
- Event   — одно событие на статью (идемпотентно по article_id);
- EventImpact — оценка влияния события на каждый связанный актив
                (direction/magnitude/rationale), идемпотентно по (event_id, asset_id).

Оценка влияния — прозрачные правила: вес типа события × тональность × релевантность.
В M4 это можно заменить дообученной моделью, сохранив интерфейс assess_impact().
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import EntityType, EventType, Sentiment
from geoanalytics.nlp.significance import type_weight
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import Article, ArticleEntity, Event, EventImpact

log = get_logger("events")


def assess_impact(
    event_type: str,
    sentiment: str | None,
    sentiment_score: float | None,
    relevance: float | None = 1.0,
) -> tuple[str, float]:
    """Возвращает (direction, magnitude ∈ [0,1]) влияния события на актив.

    Чистая функция (без БД) — основной предмет тестов.
    """
    weight = type_weight(event_type)
    rel = relevance if relevance is not None else 1.0
    score = abs(sentiment_score) if sentiment_score is not None else 0.3
    # Базовый вклад тональности: даже слабый сигнал даёт ненулевую силу.
    magnitude = round(min(1.0, weight * max(score, 0.2) * rel), 3)

    if sentiment == Sentiment.POSITIVE.value:
        direction = "positive"
    elif sentiment == Sentiment.NEGATIVE.value:
        direction = "negative"
    else:
        direction = "neutral"
    return direction, magnitude


def build_events(hours: int = 168) -> int:
    """Создаёт события и оценки влияния из новостей за окно `hours`.

    Возвращает число новых созданных событий.
    """
    created = 0
    since = datetime.now(UTC) - timedelta(hours=hours)
    with session_scope() as session:
        articles = list(session.scalars(
            select(Article).where(
                Article.published_at >= since,
                Article.event_type.isnot(None),
                Article.event_type != EventType.OTHER.value,
            )
        ))
        for art in articles:
            event = _ensure_event(session, art)
            if event is None:
                continue
            created += _build_impacts(session, art, event)
    log.info("events_built", events=created)
    return created


def _ensure_event(session: Session, art: Article) -> Event | None:
    """Создаёт Event для статьи (идемпотентно по article_id).

    Б8: если статью переклассифицировали (rescore/reclassify), тип события
    досинхронизируется — иначе Event навсегда остаётся со старым типом."""
    existing = session.scalars(select(Event).where(Event.article_id == art.id)).first()
    if existing is not None:
        if art.event_type and existing.event_type != art.event_type:
            log.info("event_type_resynced", event_id=existing.id,
                     old=existing.event_type, new=art.event_type)
            existing.event_type = art.event_type
        return existing
    event = Event(
        article_id=art.id,
        event_type=art.event_type,
        title=art.title[:512],
        occurred_at=art.published_at,
        summary=(art.text or "")[:2000] or None,
    )
    session.add(event)
    session.flush()
    return event


def _build_impacts(session: Session, art: Article, event: Event) -> int:
    """Создаёт/обновляет EventImpact по ЖИВЫМ salient-связям новости. 1, если был импакт.

    Только salient-связи (salient IS NOT FALSE) — нерелевантные упоминания не порождают
    импактов (согласовано с фильтром чтения top_impacts_for_asset). При конфликте
    (event_id, asset_id) — UPDATE direction/magnitude/rationale: после reaspect тональность
    связи меняется, и импакт ОБЯЗАН переотразить её, иначе копится рассинхрон (мина
    устаревших импактов, model-data-errors #1)."""
    links = session.scalars(
        select(ArticleEntity).where(
            ArticleEntity.article_id == art.id,
            ArticleEntity.entity_type == "asset",
            ArticleEntity.salient.isnot(False),
        )
    )
    any_impact = False
    for link in links:
        # F1 (Волна 2): направление — от тональности СВЯЗИ (относительно актива),
        # а не статьи (Б2: «Сбер обыграл ВТБ» — позитив SBER, негатив VTBR).
        direction, magnitude = assess_impact(
            art.event_type, link.sentiment or art.sentiment,
            art.sentiment_score, link.relevance,
        )
        stmt = pg_insert(EventImpact).values(
            event_id=event.id, asset_id=link.entity_id,
            direction=direction, magnitude=magnitude,
            rationale=art.title[:512],
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_event_impact",
            set_={"direction": stmt.excluded.direction,
                  "magnitude": stmt.excluded.magnitude,
                  "rationale": stmt.excluded.rationale},
        )
        session.execute(stmt)
        any_impact = True
    return 1 if any_impact else 0


def reconcile_impacts(session: Session, *, article_ids: list[int] | None = None) -> dict:
    """Настоящий фикс мины устаревших EventImpact (model-data-errors #1).

    relink/reaspect меняют связи статья↔актив (отвязывают актив, снимают salient, меняют
    тональность), но импакты исторически не пересоздавались — таблица копила «призраков»
    (события не про этот актив) и неверные знаки. Здесь два прохода:
    1) PRUNE — удалить импакты без живой salient-связи (отвязанные/неsalient);
    2) REBUILD — перестроить импакты событий по текущим связям (`_build_impacts` с upsert →
       свежие direction/magnitude).
    `article_ids=None` — по всем (джоб-чистка); иначе только по затронутым статьям
    (инкрементально из relink/reaspect). Возвращает {'pruned', 'rebuilt'}.
    """
    # PRUNE: id импактов без живой salient-связи (JOIN на событие + коррелированный
    # NOT EXISTS на связь — точное зеркало проверочного запроса; собираем id и удаляем
    # по ним, чтобы избежать сюрпризов авто-корреляции вложенных exists в DELETE).
    no_live_link = ~exists(
        select(1).select_from(ArticleEntity).where(and_(
            ArticleEntity.article_id == Event.article_id,
            ArticleEntity.entity_type == EntityType.ASSET.value,
            ArticleEntity.entity_id == EventImpact.asset_id,
            ArticleEntity.salient.isnot(False),
        ))
    )
    stale_q = (
        select(EventImpact.id)
        .join(Event, Event.id == EventImpact.event_id)
        .where(no_live_link)
    )
    if article_ids is not None:
        stale_q = stale_q.where(Event.article_id.in_(article_ids))
    stale_ids = list(session.scalars(stale_q))
    pruned = 0
    if stale_ids:
        pruned = session.execute(
            delete(EventImpact).where(EventImpact.id.in_(stale_ids))
        ).rowcount or 0

    rebuilt = 0
    ev_q = select(Event, Article).join(Article, Article.id == Event.article_id)
    if article_ids is not None:
        ev_q = ev_q.where(Event.article_id.in_(article_ids))
    for event, art in session.execute(ev_q).all():
        rebuilt += _build_impacts(session, art, event)
    log.info("reconcile_impacts", pruned=pruned, rebuilt=rebuilt,
             scope="all" if article_ids is None else len(article_ids))
    return {"pruned": pruned, "rebuilt": rebuilt}


def top_impacts_for_asset(session: Session, asset_id: int, hours: int = 168,
                          limit: int = 5) -> list[dict]:
    """Самые значимые события, повлиявшие на актив за окно."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    # Article + ЖИВАЯ связь статья↔актив: EventImpact не инвалидируется при переразметке
    # связей (geo relink), поэтому остаются СТАРЫЕ импакты на статьи, уже отвязанные от
    # актива (мина: ложные «новости не про этот актив», напр. у GMKN). Inner-join на
    # ArticleEntity (salient) показывает только события, чьи статьи ВСЁ ЕЩЁ привязаны к
    # активу. Это и даёт url для кликабельного заголовка.
    rows = session.execute(
        select(EventImpact, Event, Article.url)
        .join(Event, Event.id == EventImpact.event_id)
        .join(Article, Article.id == Event.article_id)
        .join(ArticleEntity, and_(
            ArticleEntity.article_id == Event.article_id,
            ArticleEntity.entity_type == EntityType.ASSET.value,
            ArticleEntity.entity_id == asset_id,
            ArticleEntity.salient.isnot(False),
        ))
        .where(
            EventImpact.asset_id == asset_id,
            Event.occurred_at >= since,
        )
        .order_by(EventImpact.magnitude.desc())
        .limit(limit)
    )
    return [
        {"title": ev.title, "type": ev.event_type, "url": url,
         "direction": imp.direction, "magnitude": imp.magnitude}
        for imp, ev, url in rows
    ]
