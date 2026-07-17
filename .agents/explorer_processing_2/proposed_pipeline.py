"""Инжест и конвейерная обработка документов из raw-слоя (новости, котировки, макро)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

# Импорты из общего модуля вспомогательных функций
from geoanalytics.processing.common import (
    _aspect_links,
    _compute_significance,
    _embed_batch,
    _extra_entity_rows,
    _load_asset_cache,
    _pipeline_degraded,
    _source_kind,
    _store_forecasts,
    _to_float,
    build_full_text,
)
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from config.settings import get_settings
from geoanalytics.core.dates import parse_cbr_date, parse_moex_systime, parse_rss_date
from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import EntityType, EventType, SourceKind
from geoanalytics.nlp import (
    classify,
    forecast,
    ner,
    numeric,
    rumor,
    sentiment,
    temporal,
)
from geoanalytics.nlp.embeddings import get_embedder
from geoanalytics.nlp.entity_linking import EntityIndex
from geoanalytics.nlp.text import clean_text
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import (
    Article,
    ArticleEntity,
    ArticleNumber,
    Asset,
    FxRate,
    MacroSeries,
    Price,
    RawDocument,
)
from geoanalytics.storage.repositories import normalized_hash

log = get_logger("processing")


@dataclass
class ProcessResult:
    articles: int = 0
    prices: int = 0
    macro: int = 0
    fx: int = 0
    skipped: int = 0
    duplicates: int = 0
    deferred: int = 0   # Б4: шумовой скип отложен (модель деградирована) — не помечен processed
    errors: int = 0
    by_source: dict[str, int] = field(default_factory=dict)


def _is_duplicate(session: Session, content_hash: str, window_hours: int) -> bool:
    """Есть ли за окно `window_hours` статья с тем же нормализованным хешем заголовка."""
    since = datetime.now(UTC) - timedelta(hours=window_hours)
    return session.scalar(
        select(Article.id).where(
            Article.content_hash == content_hash,
            Article.published_at >= since,
        ).limit(1)
    ) is not None


def _process_news(session: Session, doc: RawDocument, index: EntityIndex,
                  result: ProcessResult, asset_cache: dict[int, Asset],
                  pending_embeddings: list[tuple[Article, str]],
                  degraded: bool = False) -> bool:
    """Обрабатывает новостной raw-документ. Возвращает, помечать ли его processed."""
    payload = doc.payload or {}
    title = clean_text(payload.get("title"))
    body = clean_text(payload.get("summary"))
    if not title:
        result.skipped += 1
        return True
    
    # Использование новой helper функции build_full_text
    full_text = build_full_text(title, body)

    settings = get_settings()
    chash = normalized_hash(title)
    if settings.dedup_window_hours and _is_duplicate(session, chash, settings.dedup_window_hours):
        result.duplicates += 1
        return True

    label, score = sentiment.analyze(full_text)
    event_type = classify.classify_event(full_text)
    mentions = ner.extract_entities(full_text)
    links = index.match(full_text, [m.normal for m in mentions])
    significance = _compute_significance(
        event_type.value, score, [link.relevance for link in links], full_text
    )

    if (significance < settings.min_significance and not links
            and event_type in (EventType.OTHER, EventType.NOISE)):
        if degraded:
            result.deferred += 1
            return False
        result.skipped += 1
        return True

    published = parse_rss_date(payload.get("published"))
    t_status, t_date = temporal.temporal_anchor(
        full_text, (published or datetime.now(UTC)).date()
    )
    factuality, _ = rumor.classify_factuality(full_text, temporal_status=t_status)
    is_fc = forecast.is_forecast_post(
        title, body, channel=payload.get("channel"), temporal_status=t_status
    )

    article = Article(
        raw_id=doc.id,
        source=doc.source,
        source_ref=payload.get("channel"),
        url=payload.get("url"),
        content_hash=chash,
        title=title[:1024],
        text=body or title,
        published_at=published,
        sentiment=label.value,
        sentiment_score=score,
        event_type=event_type.value,
        significance=significance,
        temporal_status=t_status,
        event_date=t_date,
        factuality=factuality,
        is_forecast=is_fc,
    )
    session.add(article)
    session.flush()

    aspect_by_asset = _aspect_links(links, full_text, asset_cache, label.value)
    salient_asset_ids: list[int] = []
    for link in links:
        link_sent, link_salient = aspect_by_asset.get(
            (link.entity_type.value, link.entity_id), (label.value, None)
        )
        if link.entity_type == EntityType.ASSET and link_salient is not False:
            salient_asset_ids.append(link.entity_id)
        session.add(ArticleEntity(
            article_id=article.id,
            entity_type=link.entity_type.value,
            entity_id=link.entity_id,
            mention=link.mention[:256],
            sentiment=link_sent,
            relevance=link.relevance,
            salient=link_salient,
        ))
    for etype, eid, mention, rel in _extra_entity_rows(session, links, full_text, asset_cache):
        session.add(ArticleEntity(
            article_id=article.id, entity_type=etype, entity_id=eid,
            mention=mention[:256], sentiment=label.value, relevance=rel,
        ))

    facts = numeric.extract_numbers(full_text)
    for fact in facts:
        session.add(ArticleNumber(
            article_id=article.id, kind=fact.kind, value=fact.value,
            unit=fact.unit, snippet=fact.snippet,
        ))
    if is_fc:
        _store_forecasts(session, article.id, facts, salient_asset_ids,
                         t_date, payload.get("channel"))

    pending_embeddings.append((article, full_text))
    result.articles += 1
    return True


def _process_market(session: Session, doc: RawDocument, result: ProcessResult) -> None:
    p = doc.payload or {}
    ticker = p.get("ticker")
    if not ticker:
        result.skipped += 1
        return

    asset = session.scalars(select(Asset).where(Asset.ticker == ticker)).first()
    if asset is None:
        asset = Asset(ticker=ticker, name=p.get("name") or ticker, isin=p.get("isin"),
                      kind="share", board=p.get("board"))
        session.add(asset)
        session.flush()
        log.warning("asset_created_without_company", ticker=ticker)
    elif p.get("isin") and not asset.isin:
        asset.isin = p.get("isin")

    last = _to_float(p.get("last"))
    ts = parse_moex_systime(p.get("updated"))
    if last is None or ts is None:
        result.skipped += 1
        return

    stmt = (
        pg_insert(Price)
        .values(
            asset_id=asset.id, ts=ts, interval="1d",
            open=_to_float(p.get("open")) or last,
            high=_to_float(p.get("high")) or last,
            low=_to_float(p.get("low")) or last,
            close=last,
            volume=_to_float(p.get("volume")),
        )
        .on_conflict_do_nothing(constraint="uq_price_point")
    )
    if session.execute(stmt).rowcount:
        result.prices += 1


def _process_macro(session: Session, doc: RawDocument, result: ProcessResult) -> None:
    p = doc.payload or {}
    kind = p.get("kind")
    if kind == "fx":
        ts = parse_cbr_date(p.get("date"))
        value = _to_float(p.get("value"))
        if ts is None or value is None:
            result.skipped += 1
            return
        stmt = (
            pg_insert(FxRate)
            .values(currency=p.get("currency"), ts=ts, value=value)
            .on_conflict_do_nothing(constraint="uq_fx_point")
        )
        if session.execute(stmt).rowcount:
            result.fx += 1
    elif kind == "macro":
        ts = parse_cbr_date(p.get("date"))
        value = _to_float(p.get("value"))
        if ts is None or value is None:
            result.skipped += 1
            return
        stmt = (
            pg_insert(MacroSeries)
            .values(indicator=p.get("indicator"), ts=ts, value=value,
                    unit=p.get("unit") or "%")
            .on_conflict_do_nothing(constraint="uq_macro_point")
        )
        if session.execute(stmt).rowcount:
            result.macro += 1
    else:
        result.skipped += 1


def process_pending(batch_size: int = 500) -> ProcessResult:
    """Обрабатывает накопленные необработанные raw-документы."""
    result = ProcessResult()
    with session_scope() as session:
        index = EntityIndex(session)
        asset_cache = _load_asset_cache(session)
        embedder = get_embedder()
        degraded = _pipeline_degraded()
        if degraded:
            log.warning("processing_models_degraded_defer_noise_skips")
        pending_embeddings: list[tuple[Article, str]] = []
        stmt = (
            select(RawDocument)
            .where(RawDocument.processed.is_(False))
            .order_by(RawDocument.fetched_at)
            .limit(batch_size)
        )
        docs = list(session.scalars(stmt))
        for doc in docs:
            kind = _source_kind(doc.source)
            embed_mark = len(pending_embeddings)
            try:
                with session.begin_nested():
                    processed = True
                    if kind == SourceKind.NEWS:
                        processed = _process_news(session, doc, index, result,
                                                  asset_cache, pending_embeddings, degraded)
                    elif kind == SourceKind.MARKET:
                        _process_market(session, doc, result)
                    elif kind == SourceKind.MACRO:
                        _process_macro(session, doc, result)
                    else:
                        result.skipped += 1
                    doc.processed = processed
                result.by_source[doc.source] = result.by_source.get(doc.source, 0) + 1
            except Exception as exc:  # noqa: BLE001
                del pending_embeddings[embed_mark:]
                result.errors += 1
                log.error("process_doc_failed", doc_id=doc.id, source=doc.source, error=str(exc))
        _embed_batch(session, embedder, [(a.id, t) for a, t in pending_embeddings])
    log.info("process_done", articles=result.articles, prices=result.prices,
             macro=result.macro, fx=result.fx, deferred=result.deferred,
             errors=result.errors)
    return result


@dataclass
class ReprocessResult:
    reopened: int = 0
    error: str | None = None


def reprocess_skipped(limit: int | None = None) -> ReprocessResult:
    """Б4 follow-up: переоткрыть новостные raw-доки, помеченные processed, но НЕ давшие статью."""
    from geoanalytics.connectors.registry import all_connectors

    result = ReprocessResult()
    news_sources = [c.name for c in all_connectors() if c.kind == SourceKind.NEWS]
    if not news_sources:
        return result
    with session_scope() as session:
        has_article = (
            select(Article.id).where(Article.raw_id == RawDocument.id).exists()
        )
        stmt = (
            select(RawDocument).where(
                RawDocument.source.in_(news_sources),
                RawDocument.processed.is_(True),
                ~has_article,
            ).order_by(RawDocument.fetched_at.desc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        for doc in session.scalars(stmt):
            doc.processed = False
            result.reopened += 1
    log.info("reprocess_skipped_done", reopened=result.reopened)
    return result
