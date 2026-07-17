"""Модуль переразметки, перелинковки и обогащения уже сохраненных в БД статей."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from geoanalytics.processing.common import (
    _compute_significance,
    _embed_batch,
    _extra_entity_rows,
    _load_asset_cache,
    _store_forecasts,
    build_full_text,
    paginate_query,
)
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import EntityType
from geoanalytics.nlp import (
    aspect,
    classify,
    forecast,
    ner,
    numeric,
    rumor,
    sentiment,
    temporal,
)
from geoanalytics.nlp.embeddings import get_embedder
from geoanalytics.storage.models import (
    Article,
    ArticleEntity,
    ArticleNumber,
    Asset,
    Embedding,
)

log = get_logger("processing")


@dataclass
class RelinkResult:
    """Итог перелинковки уже сохранённых статей."""

    articles: int = 0
    links: int = 0
    embeddings: int = 0


def relink_existing(batch_size: int = 2000) -> RelinkResult:
    """Повторно прогоняет NER + entity-linking (и при наличии — эмбеддинги) по уже

    сохранённым статьям.
    """
    result = RelinkResult()
    # Так как этот метод не использует пагинацию по offset/limit в исходном файле
    # (он берет фиксированную порцию limit(batch_size)), мы оставляем исходную структуру.
    from geoanalytics.storage.db import (
        session_scope,  # Локальный импорт во избежание циклической зависимости
    )
    with session_scope() as session:
        index = EntityIndex(session)
        asset_cache = _load_asset_cache(session)
        embedder = get_embedder()
        have_embedding = set(session.scalars(select(Embedding.article_id)))
        to_embed: list[tuple[int, str]] = []
        articles = list(session.scalars(select(Article).limit(batch_size)))
        for art in articles:
            result.articles += 1
            full_text = build_full_text(art.title, art.text)
            mentions = ner.extract_entities(full_text)
            links = index.match(full_text, [m.normal for m in mentions])
            for link in links:
                stmt = (
                    pg_insert(ArticleEntity)
                    .values(
                        article_id=art.id,
                        entity_type=link.entity_type.value,
                        entity_id=link.entity_id,
                        mention=link.mention[:256],
                        sentiment=art.sentiment,
                        relevance=link.relevance,
                    )
                    .on_conflict_do_nothing(constraint="uq_artent")
                )
                if session.execute(stmt).rowcount:
                    result.links += 1
            for etype, eid, mention, rel in _extra_entity_rows(
                    session, links, full_text, asset_cache):
                stmt = (
                    pg_insert(ArticleEntity)
                    .values(article_id=art.id, entity_type=etype, entity_id=eid,
                            mention=mention[:256], sentiment=art.sentiment, relevance=rel)
                    .on_conflict_do_nothing(constraint="uq_artent")
                )
                if session.execute(stmt).rowcount:
                    result.links += 1
            art.significance = _compute_significance(
                art.event_type, art.sentiment_score,
                [link.relevance for link in links], full_text,
            )
            if embedder is not None and art.id not in have_embedding:
                to_embed.append((art.id, full_text))
        result.embeddings = _embed_batch(session, embedder, to_embed)
        from geoanalytics.context.events import reconcile_impacts
        reconcile_impacts(session, article_ids=[a.id for a in articles])
    log.info("relink_done", articles=result.articles, links=result.links,
             embeddings=result.embeddings)
    return result


from geoanalytics.nlp.entity_linking import EntityIndex  # noqa: E402

RESCORE_STAGES: tuple[str, ...] = ("sentiment", "events", "significance")


@dataclass
class RescoreResult:
    """Итог переразметки уже сохранённых статей обновлёнными моделями NLP."""

    articles: int = 0
    sentiment_changed: int = 0
    event_changed: int = 0
    significance_changed: int = 0
    errors: int = 0
    dry_run: bool = False
    sentiment_before: Counter = field(default_factory=Counter)
    sentiment_after: Counter = field(default_factory=Counter)


def _rescore_article(session: Session, art: Article, relevances: list[float], *,
                     stages: tuple[str, ...], do_significance: bool,
                     result: RescoreResult, dry_run: bool) -> None:
    """Пересчитывает выбранные модельные поля одной статьи."""
    result.articles += 1
    full_text = build_full_text(art.title, art.text)

    label_val = art.sentiment
    score = art.sentiment_score
    event_type = art.event_type
    if "sentiment" in stages:
        label, score = sentiment.analyze(full_text)
        label_val = label.value
    if "events" in stages:
        event_type = classify.classify_event(full_text).value

    result.sentiment_before[art.sentiment or "none"] += 1
    result.sentiment_after[label_val or "none"] += 1
    if label_val != art.sentiment:
        result.sentiment_changed += 1
    if event_type != art.event_type:
        result.event_changed += 1

    new_significance = art.significance
    if do_significance:
        new_significance = _compute_significance(event_type, score, relevances, full_text)
        if art.significance is None or abs(new_significance - art.significance) > 1e-9:
            result.significance_changed += 1

    if dry_run:
        return
    art.sentiment = label_val
    art.sentiment_score = score
    art.event_type = event_type
    art.significance = new_significance
    if "sentiment" in stages:
        stmt = update(ArticleEntity).where(ArticleEntity.article_id == art.id)
        if aspect._get_sentiment_model() is not None:
            stmt = stmt.where(ArticleEntity.entity_type != EntityType.ASSET.value)
        session.execute(stmt.values(sentiment=label_val))


def rescore_existing(
    stages: Iterable[str] = ("sentiment", "significance"),
    *,
    batch_size: int = 1000,
    limit: int | None = None,
    dry_run: bool = False,
) -> RescoreResult:
    """Переразмечает уже сохранённые статьи ОБНОВЛЁННЫМИ моделями NLP."""
    stages = tuple(stages)
    unknown = set(stages) - set(RESCORE_STAGES)
    if unknown:
        raise ValueError(f"Неизвестные стадии: {sorted(unknown)}; допустимы {RESCORE_STAGES}.")
    if not stages:
        raise ValueError("Не выбрано ни одной стадии переразметки.")
    do_significance = bool({"sentiment", "events", "significance"} & set(stages))

    result = RescoreResult(dry_run=dry_run)

    def query_fn(s):
        return select(Article).order_by(Article.id)

    # Использование универсального paginate_query
    for session, articles in paginate_query(
        query_fn, batch_size=batch_size, limit=limit, scalar=True
    ):
        ids = [a.id for a in articles]
        rel_map: dict[int, list[float]] = defaultdict(list)
        for aid, rel in session.execute(
            select(ArticleEntity.article_id, ArticleEntity.relevance)
            .where(ArticleEntity.article_id.in_(ids))
        ).all():
            rel_map[aid].append(rel or 0.0)

        for art in articles:
            try:
                with session.begin_nested():
                    _rescore_article(
                        session, art, rel_map.get(art.id, []),
                        stages=stages, do_significance=do_significance,
                        result=result, dry_run=dry_run,
                    )
            except Exception as exc:  # noqa: BLE001
                result.errors += 1
                log.error("rescore_article_failed", article_id=art.id, error=str(exc))

    log.info("rescore_done", articles=result.articles,
             sentiment_changed=result.sentiment_changed, event_changed=result.event_changed,
             significance_changed=result.significance_changed, errors=result.errors,
             dry_run=dry_run)
    return result


@dataclass
class ReaspectResult:
    """Итог переразметки asset-связей aspect-моделями (F1/F2)."""

    links: int = 0
    sentiment_changed: int = 0
    salient_set: int = 0
    errors: int = 0


def reaspect_existing(limit: int | None = None, batch_size: int = 500) -> ReaspectResult:
    """Переразмечает СУЩЕСТВУЮЩИЕ связи статья↔актив aspect-моделями (F1/F2)."""
    result = ReaspectResult()
    if aspect._get_sentiment_model() is None and aspect._get_saliency_model() is None:
        log.warning("reaspect_no_models")
        return result

    def query_fn(s):
        return (
            select(ArticleEntity, Article.title, Article.text, Asset.ticker, Asset.name)
            .join(Article, Article.id == ArticleEntity.article_id)
            .join(Asset, Asset.id == ArticleEntity.entity_id)
            .where(ArticleEntity.entity_type == EntityType.ASSET.value)
            .order_by(ArticleEntity.id)
        )

    # Использование универсального paginate_query
    for _session, rows in paginate_query(
        query_fn, batch_size=batch_size, limit=limit, scalar=False
    ):
        for link, title, body, ticker, name in rows:
            result.links += 1
            try:
                full_text = build_full_text(title, body)
                sent, salient = aspect.analyze_pair(
                    aspect.aspect_name(ticker, name), full_text
                )
                if sent is not None and sent != link.sentiment:
                    link.sentiment = sent
                    result.sentiment_changed += 1
                if salient is not None and salient != link.salient:
                    link.salient = salient
                    result.salient_set += 1
            except Exception as exc:  # noqa: BLE001
                result.errors += 1
                log.error("reaspect_failed", link_id=link.id, error=str(exc))

    if result.sentiment_changed or result.salient_set:
        from geoanalytics.context.events import reconcile_impacts

        # reaspect_existing может вызываться с session,
        # но здесь мы открываем новую сессию через session_scope
        from geoanalytics.storage.db import session_scope
        with session_scope() as session:
            reconcile_impacts(session)

    log.info("reaspect_done", links=result.links,
             sentiment_changed=result.sentiment_changed,
             salient_set=result.salient_set, errors=result.errors)
    return result


@dataclass
class RetemporalResult:
    articles: int = 0
    status_set: int = 0
    date_set: int = 0
    errors: int = 0


def retemporal_existing(limit: int | None = None,
                        batch_size: int = 500) -> RetemporalResult:
    """Размечает СУЩЕСТВУЮЩИЕ статьи temporal-моделью (F3): статус + дата события."""
    result = RetemporalResult()
    if temporal._model() is None:
        log.warning("retemporal_no_model")
        return result

    def query_fn(s):
        return select(Article).order_by(Article.id)

    # Использование универсального paginate_query
    for _session, rows in paginate_query(
        query_fn, batch_size=batch_size, limit=limit, scalar=True
    ):
        for art in rows:
            result.articles += 1
            try:
                full_text = build_full_text(art.title, art.text)
                published = (art.published_at or datetime.now(UTC)).date()
                status, ev_date = temporal.temporal_anchor(full_text, published)
                if status is not None and status != art.temporal_status:
                    art.temporal_status = status
                    result.status_set += 1
                if ev_date is not None and ev_date != art.event_date:
                    art.event_date = ev_date
                    result.date_set += 1
            except Exception as exc:  # noqa: BLE001
                result.errors += 1
                log.error("retemporal_failed", article_id=art.id, error=str(exc))

    log.info("retemporal_done", articles=result.articles,
             status_set=result.status_set, date_set=result.date_set,
             errors=result.errors)
    return result


@dataclass
class RefactualityResult:
    articles: int = 0
    set_count: int = 0
    by_label: dict[str, int] = field(default_factory=dict)
    errors: int = 0


def refactuality_existing(limit: int | None = None,
                          batch_size: int = 500) -> RefactualityResult:
    """Размечает СУЩЕСТВУЮЩИЕ статьи фактологичностью F4 (fact/rumor/opinion)."""
    result = RefactualityResult()
    def query_fn(s):
        return select(Article).order_by(Article.id)

    # Использование универсального paginate_query
    for _session, rows in paginate_query(
        query_fn, batch_size=batch_size, limit=limit, scalar=True
    ):
        for art in rows:
            result.articles += 1
            try:
                full_text = build_full_text(art.title, art.text)
                label, _ = rumor.classify_factuality(
                    full_text, temporal_status=art.temporal_status
                )
                if label != art.factuality:
                    art.factuality = label
                    result.set_count += 1
                result.by_label[label] = result.by_label.get(label, 0) + 1
            except Exception as exc:  # noqa: BLE001
                result.errors += 1
                log.error("refactuality_failed", article_id=art.id, error=str(exc))

    log.info("refactuality_done", articles=result.articles,
             set_count=result.set_count, errors=result.errors)
    return result


@dataclass
class RenumericResult:
    articles: int = 0
    facts: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    errors: int = 0


def renumeric_existing(limit: int | None = None,
                       batch_size: int = 500) -> RenumericResult:
    """Извлекает числовые факты (F5) из СУЩЕСТВУЮЩИХ статей."""
    result = RenumericResult()
    def query_fn(s):
        return select(Article.id, Article.title, Article.text).order_by(Article.id)

    # Использование универсального paginate_query
    for session, rows in paginate_query(
        query_fn, batch_size=batch_size, limit=limit, scalar=False
    ):
        for art_id, title, body in rows:
            result.articles += 1
            try:
                full_text = build_full_text(title, body)
                facts = numeric.extract_numbers(full_text)
            except Exception as exc:  # noqa: BLE001
                result.errors += 1
                log.error("renumeric_failed", article_id=art_id, error=str(exc))
                continue
            for fact in facts:
                inserted = session.execute(
                    pg_insert(ArticleNumber)
                    .values(article_id=art_id, kind=fact.kind, value=fact.value,
                            unit=fact.unit, snippet=fact.snippet)
                    .on_conflict_do_nothing(constraint="uq_artnum")
                ).rowcount
                if inserted:
                    result.facts += 1
                    result.by_kind[fact.kind] = result.by_kind.get(fact.kind, 0) + 1

    log.info("renumeric_done", articles=result.articles, facts=result.facts,
             by_kind=result.by_kind, errors=result.errors)
    return result


@dataclass
class ReforecastResult:
    articles: int = 0
    marked: int = 0
    forecasts: int = 0
    errors: int = 0


def reforecast_existing(limit: int | None = None,
                        batch_size: int = 500) -> ReforecastResult:
    """F10: размечает СУЩЕСТВУЮЩИЕ брокерские статьи — is_forecast + наполнение forecasts."""
    result = ReforecastResult()
    channels = list(forecast.BROKER_CHANNELS)
    def query_fn(s):
        return (
            select(Article)
            .where(Article.source_ref.in_(channels))
            .order_by(Article.id)
        )

    # Использование универсального paginate_query
    for session, arts in paginate_query(
        query_fn, batch_size=batch_size, limit=limit, scalar=True
    ):
        for art in arts:
            result.articles += 1
            try:
                if not forecast.is_forecast_post(
                    art.title, art.text, channel=art.source_ref,
                    temporal_status=art.temporal_status,
                ):
                    continue
                if not art.is_forecast:
                    art.is_forecast = True
                    result.marked += 1
                asset_ids = list(session.scalars(
                    select(ArticleEntity.entity_id).where(
                        ArticleEntity.article_id == art.id,
                        ArticleEntity.entity_type == EntityType.ASSET.value,
                        ArticleEntity.salient.isnot(False),
                    )
                ).all())
                text = build_full_text(art.title, art.text)
                result.forecasts += _store_forecasts(
                    session, art.id, numeric.extract_numbers(text),
                    asset_ids, art.event_date, art.source_ref,
                )
            except Exception as exc:  # noqa: BLE001
                result.errors += 1
                log.error("reforecast_failed", article_id=art.id, error=str(exc))

    log.info("reforecast_done", articles=result.articles, marked=result.marked,
             forecasts=result.forecasts, errors=result.errors)
    return result
