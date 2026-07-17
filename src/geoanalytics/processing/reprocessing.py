from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

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
from geoanalytics.nlp.entity_linking import EntityIndex
from geoanalytics.processing.common import (
    ReaspectResult,
    RefactualityResult,
    ReforecastResult,
    RelinkResult,
    RenumericResult,
    RescoreResult,
    RetemporalResult,
    _embed_batch,
    _extra_entity_rows,
    _load_asset_cache,
    _store_forecasts,
    build_article_text,
    execute_reprocessing,
    log,
    paginate_query,
)
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import (
    Article,
    ArticleEntity,
    ArticleNumber,
    Asset,
    Embedding,
)


def relink_existing(batch_size: int = 2000) -> RelinkResult:
    """Повторно прогоняет NER + entity-linking (и при наличии — эмбеддинги) по уже
    сохранённым статьям.

    Нужна, когда новости были обработаны до установки NLP-моделей (NER/эмбеддер):
    raw-документы уже `processed=True`, и обычный конвейер их не трогает, поэтому
    связи article↔entity так и не появились. Здесь статьи не пересоздаются —
    только добавляются недостающие связи/эмбеддинги. Идемпотентно: дубликаты связей
    отсекает уникальный индекс `uq_artent`.
    """
    result = RelinkResult()
    with session_scope() as session:
        index = EntityIndex(session)
        asset_cache = _load_asset_cache(session)  # один запрос на батч (убирает N+1)
        embedder = get_embedder()
        have_embedding = set(session.scalars(select(Embedding.article_id)))
        to_embed: list[tuple[int, str]] = []  # копим на батч-эмбеддинг после цикла
        articles = list(session.scalars(select(Article).limit(batch_size)))

        def process_art(sess: Session, art: Article):
            result.articles += 1
            full_text = build_article_text(art)
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
                if sess.execute(stmt).rowcount:
                    result.links += 1
            # Derived-связи (сектор/страна/тема) — идемпотентно.
            for etype, eid, mention, rel in _extra_entity_rows(sess, links, full_text, asset_cache):
                stmt = (
                    pg_insert(ArticleEntity)
                    .values(
                        article_id=art.id,
                        entity_type=etype,
                        entity_id=eid,
                        mention=mention[:256],
                        sentiment=art.sentiment,
                        relevance=rel,
                    )
                    .on_conflict_do_nothing(constraint="uq_artent")
                )
                if sess.execute(stmt).rowcount:
                    result.links += 1
            # Пересчитываем значимость по освежённым связям (линковка улучшилась).
            import geoanalytics.processing as gp

            art.significance = gp._compute_significance(
                art.event_type,
                art.sentiment_score,
                [link.relevance for link in links],
                full_text,
            )
            if embedder is not None and art.id not in have_embedding:
                to_embed.append((art.id, full_text))

        execute_reprocessing(
            session,
            articles,
            process_art,
            log_error_name="relink_failed",
            error_extra_fn=lambda a: {"article_id": a.id},
        )

        # Эмбеддинги — одним батчем после цикла (5–10× быстрее на CPU), с fallback на одиночные.
        result.embeddings = _embed_batch(session, embedder, to_embed)
        # Связи изменились → привести EventImpact в соответствие (настоящий фикс мины
        # устаревших импактов, model-data-errors #1): удалить призраков, освежить знаки.
        from geoanalytics.context.events import reconcile_impacts

        reconcile_impacts(session, article_ids=[a.id for a in articles])
    log.info(
        "relink_done", articles=result.articles, links=result.links, embeddings=result.embeddings
    )
    return result


RESCORE_STAGES: tuple[str, ...] = ("sentiment", "events", "significance")


def _rescore_article(
    session: Session,
    art: Article,
    relevances: list[float],
    *,
    stages: tuple[str, ...],
    do_significance: bool,
    result: RescoreResult,
    dry_run: bool,
) -> None:
    """Пересчитывает выбранные модельные поля одной статьи.

    Связи не пересоздаются (для этого `relink_existing`): значимость считается по уже
    сохранённым релевантностям. Изменения применяются только при `dry_run=False`.
    """
    result.articles += 1
    full_text = build_article_text(art)

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
        import geoanalytics.processing as gp

        new_significance = gp._compute_significance(event_type, score, relevances, full_text)
        if art.significance is None or abs(new_significance - art.significance) > 1e-9:
            result.significance_changed += 1

    if dry_run:
        return
    art.sentiment = label_val
    art.sentiment_score = score
    art.event_type = event_type
    art.significance = new_significance
    if "sentiment" in stages:
        # Денормализованная копия тональности в связях article↔entity — держим в согласии
        # (её читают негатив-алерты и витрины, чтобы не джойнить со статьёй).
        # F1 (Волна 2): если активна aspect-модель, asset-связи несут СВОЮ тональность
        # (относительно актива) — их не затираем; для них есть `geo reaspect`.
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
    """Переразмечает уже сохранённые статьи ОБНОВЛЁННЫМИ моделями NLP — не пересоздавая
    их и не перелинковывая (для линковки есть `relink_existing`).

    Зачем: модели (тональность/классификатор/значимость) со временем меняются (напр.
    дистилляция LLM-учителя в сентимент), а исторические статьи хранят метки, проставленные
    старой моделью на инжесте. Эта функция приводит историю в соответствие текущим моделям —
    точка входа для будущих смен моделей.

    Стадии (`stages` ⊆ `RESCORE_STAGES`):
    - ``"sentiment"``    — пересчитать тональность (+ синхронизировать копию в связях);
    - ``"events"``       — пересчитать тип события (классификатор);
    - ``"significance"`` — пересчитать значимость по существующим связям.
    Значимость пересчитывается автоматически, если меняется тональность или тип события
    (она от них производная), даже если не указана явно.

    Идемпотентно (модели детерминированы), безопасно (SAVEPOINT на статью — ошибка одной
    не валит батч), батчами с коммитом на батч. `dry_run` считает и сравнивает, ничего не
    записывая. `limit` ограничивает число статей.
    """
    stages = tuple(stages)
    unknown = set(stages) - set(RESCORE_STAGES)
    if unknown:
        raise ValueError(f"Неизвестные стадии: {sorted(unknown)}; допустимы {RESCORE_STAGES}.")
    if not stages:
        raise ValueError("Не выбрано ни одной стадии переразметки.")
    do_significance = bool({"sentiment", "events", "significance"} & set(stages))

    result = RescoreResult(dry_run=dry_run)

    def fetch_fn(session: Session, offset: int, take: int) -> list[Article]:
        return list(
            session.scalars(select(Article).order_by(Article.id).offset(offset).limit(take)).all()
        )

    for session, articles in paginate_query(fetch_fn, batch_size, limit):
        # Релевантности связей для всего батча одним запросом (для значимости).
        ids = [a.id for a in articles]
        rel_map: dict[int, list[float]] = defaultdict(list)
        for aid, rel in session.execute(
            select(ArticleEntity.article_id, ArticleEntity.relevance).where(
                ArticleEntity.article_id.in_(ids)
            )
        ).all():
            rel_map[aid].append(rel or 0.0)

        def process_art(sess: Session, art: Article, rel_map=rel_map):
            _rescore_article(
                sess,
                art,
                rel_map.get(art.id, []),
                stages=stages,
                do_significance=do_significance,
                result=result,
                dry_run=dry_run,
            )

        result.errors += execute_reprocessing(
            session,
            articles,
            process_art,
            log_error_name="rescore_article_failed",
            error_extra_fn=lambda art: {"article_id": art.id},
        )

    log.info(
        "rescore_done",
        articles=result.articles,
        sentiment_changed=result.sentiment_changed,
        event_changed=result.event_changed,
        significance_changed=result.significance_changed,
        errors=result.errors,
        dry_run=dry_run,
    )
    return result


def reaspect_existing(limit: int | None = None, batch_size: int = 500) -> ReaspectResult:
    """Переразмечает СУЩЕСТВУЮЩИЕ связи статья↔актив aspect-моделями (F1/F2).

    Нужна после деплоя/смены aspect-моделей: исторические связи несут копию
    тональности статьи (Б2). Идемпотентно (модели детерминированы). Без моделей —
    no-op (нечем размечать).
    """
    result = ReaspectResult()
    if aspect._get_sentiment_model() is None and aspect._get_saliency_model() is None:
        log.warning("reaspect_no_models")
        return result

    def fetch_fn(session: Session, offset: int, take: int):
        return session.execute(
            select(ArticleEntity, Article.title, Article.text, Asset.ticker, Asset.name)
            .join(Article, Article.id == ArticleEntity.article_id)
            .join(Asset, Asset.id == ArticleEntity.entity_id)
            .where(ArticleEntity.entity_type == EntityType.ASSET.value)
            .order_by(ArticleEntity.id)
            .offset(offset)
            .limit(take)
        ).all()

    def process_row(sess: Session, row: tuple):
        link, title, body, ticker, name = row
        result.links += 1
        full_text = build_article_text(title, body)
        sent, salient = aspect.analyze_pair(aspect.aspect_name(ticker, name), full_text)
        if sent is not None and sent != link.sentiment:
            link.sentiment = sent
            result.sentiment_changed += 1
        if salient is not None and salient != link.salient:
            link.salient = salient
            result.salient_set += 1

    for session, rows in paginate_query(fetch_fn, batch_size, limit):
        result.errors += execute_reprocessing(
            session,
            rows,
            process_row,
            log_error_name="reaspect_failed",
            error_extra_fn=lambda r: {"link_id": r[0].id},
        )

    # Тональность/salient связей изменились → переотразить во всех импактах и убрать
    # призраков (model-data-errors #1). reaspect меняет связи глобально → полная сверка.
    if result.sentiment_changed or result.salient_set:
        from geoanalytics.context.events import reconcile_impacts

        with session_scope() as session:
            reconcile_impacts(session)
    log.info(
        "reaspect_done",
        links=result.links,
        sentiment_changed=result.sentiment_changed,
        salient_set=result.salient_set,
        errors=result.errors,
    )
    return result


def retemporal_existing(limit: int | None = None, batch_size: int = 500) -> RetemporalResult:
    """Размечает СУЩЕСТВУЮЩИЕ статьи temporal-моделью (F3): статус + дата события.

    Нужна после деплоя/смены temporal-модели. Идемпотентно (модель и экстрактор
    детерминированы). Без модели — no-op.
    """
    result = RetemporalResult()
    if temporal._model() is None:
        log.warning("retemporal_no_model")
        return result

    def fetch_fn(session: Session, offset: int, take: int) -> list[Article]:
        return list(
            session.scalars(select(Article).order_by(Article.id).offset(offset).limit(take)).all()
        )

    def process_art(sess: Session, art: Article):
        result.articles += 1
        full_text = build_article_text(art)
        published = (art.published_at or datetime.now(UTC)).date()
        status, ev_date = temporal.temporal_anchor(full_text, published)
        if status is not None and status != art.temporal_status:
            art.temporal_status = status
            result.status_set += 1
        if ev_date is not None and ev_date != art.event_date:
            art.event_date = ev_date
            result.date_set += 1

    for session, rows in paginate_query(fetch_fn, batch_size, limit):
        result.errors += execute_reprocessing(
            session,
            rows,
            process_art,
            log_error_name="retemporal_failed",
            error_extra_fn=lambda a: {"article_id": a.id},
        )

    log.info(
        "retemporal_done",
        articles=result.articles,
        status_set=result.status_set,
        date_set=result.date_set,
        errors=result.errors,
    )
    return result


def refactuality_existing(limit: int | None = None, batch_size: int = 500) -> RefactualityResult:
    """Размечает СУЩЕСТВУЮЩИЕ статьи фактологичностью F4 (fact/rumor/opinion).

    Нужна после деплоя/изменения правил nlp/rumor.py. Детерминирована и идемпотентна;
    усиливается уже проставленным temporal_status (прогноз → rumor).
    """
    result = RefactualityResult()

    def fetch_fn(session: Session, offset: int, take: int) -> list[Article]:
        return list(
            session.scalars(select(Article).order_by(Article.id).offset(offset).limit(take)).all()
        )

    def process_art(sess: Session, art: Article):
        result.articles += 1
        full_text = build_article_text(art)
        label, _ = rumor.classify_factuality(full_text, temporal_status=art.temporal_status)
        if label != art.factuality:
            art.factuality = label
            result.set_count += 1
        result.by_label[label] = result.by_label.get(label, 0) + 1

    for session, rows in paginate_query(fetch_fn, batch_size, limit):
        result.errors += execute_reprocessing(
            session,
            rows,
            process_art,
            log_error_name="refactuality_failed",
            error_extra_fn=lambda a: {"article_id": a.id},
        )

    log.info(
        "refactuality_done",
        articles=result.articles,
        set_count=result.set_count,
        errors=result.errors,
    )
    return result


def renumeric_existing(limit: int | None = None, batch_size: int = 500) -> RenumericResult:
    """Извлекает числовые факты (F5) из СУЩЕСТВУЮЩИХ статей.

    Нужна после деплоя/изменения правил nlp/numeric.py. Идемпотентно:
    upsert с ON CONFLICT DO NOTHING по uq_artnum.
    """
    result = RenumericResult()

    def fetch_fn(session: Session, offset: int, take: int):
        return session.execute(
            select(Article.id, Article.title, Article.text)
            .order_by(Article.id)
            .offset(offset)
            .limit(take)
        ).all()

    def process_row(sess: Session, row: tuple):
        art_id, title, body = row
        result.articles += 1
        facts = numeric.extract_numbers(build_article_text(title, body))
        for fact in facts:
            inserted = sess.execute(
                pg_insert(ArticleNumber)
                .values(
                    article_id=art_id,
                    kind=fact.kind,
                    value=fact.value,
                    unit=fact.unit,
                    snippet=fact.snippet,
                )
                .on_conflict_do_nothing(constraint="uq_artnum")
            ).rowcount
            if inserted:
                result.facts += 1
                result.by_kind[fact.kind] = result.by_kind.get(fact.kind, 0) + 1

    for session, rows in paginate_query(fetch_fn, batch_size, limit):
        result.errors += execute_reprocessing(
            session,
            rows,
            process_row,
            log_error_name="renumeric_failed",
            error_extra_fn=lambda r: {"article_id": r[0]},
        )

    log.info(
        "renumeric_done",
        articles=result.articles,
        facts=result.facts,
        by_kind=result.by_kind,
        errors=result.errors,
    )
    return result


def reforecast_existing(limit: int | None = None, batch_size: int = 500) -> ReforecastResult:
    """F10: размечает СУЩЕСТВУЮЩИЕ брокерские статьи — is_forecast + наполнение forecasts.

    Нужна после деплоя роутера/правил target_price. Идемпотентно (uq_forecast,
    повторная пометка no-op). Ходит только по статьям брокерских каналов (source_ref)."""
    result = ReforecastResult()
    channels = list(forecast.BROKER_CHANNELS)

    def fetch_fn(session: Session, offset: int, take: int) -> list[Article]:
        return list(
            session.scalars(
                select(Article)
                .where(Article.source_ref.in_(channels))
                .order_by(Article.id)
                .offset(offset)
                .limit(take)
            ).all()
        )

    def process_art(sess: Session, art: Article):
        result.articles += 1
        if not forecast.is_forecast_post(
            art.title,
            art.text,
            channel=art.source_ref,
            temporal_status=art.temporal_status,
        ):
            return
        if not art.is_forecast:
            art.is_forecast = True
            result.marked += 1
        asset_ids = list(
            sess.scalars(
                select(ArticleEntity.entity_id).where(
                    ArticleEntity.article_id == art.id,
                    ArticleEntity.entity_type == EntityType.ASSET.value,
                    ArticleEntity.salient.isnot(False),
                )
            ).all()
        )
        text = build_article_text(art)
        result.forecasts += _store_forecasts(
            sess,
            art.id,
            numeric.extract_numbers(text),
            asset_ids,
            art.event_date,
            art.source_ref,
        )

    for session, arts in paginate_query(fetch_fn, batch_size, limit):
        result.errors += execute_reprocessing(
            session,
            arts,
            process_art,
            log_error_name="reforecast_failed",
            error_extra_fn=lambda a: {"article_id": a.id},
        )

    log.info(
        "reforecast_done",
        articles=result.articles,
        marked=result.marked,
        forecasts=result.forecasts,
        errors=result.errors,
    )
    return result
