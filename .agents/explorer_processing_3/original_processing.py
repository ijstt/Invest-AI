"""Конвейер обработки raw-слоя → нормализованные таблицы.

Диспетчеризация по типу источника:
- NEWS  (interfax) → очистка, NER, entity-linking, сентимент, классификация,
                     эмбеддинг → Article + ArticleEntity + Embedding;
- MARKET(moex)     → upsert Asset + дневная свеча Price;
- MACRO (cbr)      → MacroSeries (ставка) / FxRate (курсы).

После обработки raw-документ помечается processed=True.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, selectinload

from config.settings import get_settings
from geoanalytics.connectors.registry import get_connector
from geoanalytics.core.dates import parse_cbr_date, parse_moex_systime, parse_rss_date
from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import EntityType, EventType, SourceKind
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
from geoanalytics.nlp.significance import predict_significance, significance_score
from geoanalytics.nlp.text import clean_text
from geoanalytics.nlp.themes import classify_themes
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import (
    Article,
    ArticleEntity,
    ArticleNumber,
    Asset,
    Embedding,
    FxRate,
    MacroSeries,
    MacroTheme,
    Price,
    RawDocument,
)
from geoanalytics.storage.repositories import ForecastRepository, normalized_hash

log = get_logger("processing")


def _load_asset_cache(session: Session) -> dict[int, Asset]:
    """Все активы с предзагруженными компаниями — один запрос на батч. Активов мало
    (эмитенты), поэтому грузим целиком и убираем N+1 из горячего цикла `_extra_entity_rows`."""
    assets = session.scalars(select(Asset).options(selectinload(Asset.company)))
    return {a.id: a for a in assets}


def _extra_entity_rows(session: Session, links: list, full_text: str,
                       asset_cache: dict[int, Asset]) -> list[tuple]:
    """Доп. связи новости сверх прямых упоминаний: derived сектор/страна (через компанию
    актива) и макро-темы (по ключевым словам). Возвращает (entity_type, id, mention, rel).

    Это и есть «связь новость↔объект не только по тикерам»: новость про SBER связывается
    и с сектором «Банки», и со страной РФ; новость про санкции — с темой «Санкции».

    `asset_cache` (id→Asset с подгруженной company) строится один раз на батч — без него
    тут был N+1: `session.get(Asset)` + ленивая `asset.company` на каждую связь.
    """
    seen = {(link.entity_type.value, link.entity_id) for link in links}
    extra: list[tuple] = []
    for link in links:
        if link.entity_type != EntityType.ASSET:
            continue
        asset = asset_cache.get(link.entity_id)
        comp = asset.company if asset else None
        if not comp:
            continue
        # derived-связь слабее прямой (×0.8): тематическая, не прямое упоминание.
        for etype, eid in (("sector", comp.sector_id), ("country", comp.country_id)):
            if eid and (etype, eid) not in seen:
                seen.add((etype, eid))
                extra.append((etype, eid, asset.ticker, round(link.relevance * 0.8, 3)))

    tnames = classify_themes(full_text)
    if tnames:
        for th in session.scalars(select(MacroTheme).where(MacroTheme.name.in_(tnames))):
            if ("macro_theme", th.id) not in seen:
                seen.add(("macro_theme", th.id))
                extra.append(("macro_theme", th.id, th.name, 0.8))
    return extra


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


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _source_kind(source: str) -> SourceKind | None:
    """Тип источника по его имени (через реестр коннекторов)."""
    try:
        return get_connector(source).kind
    except KeyError:
        return None


def _compute_significance(event_type: str, score: float | None,
                          relevances: list[float], text: str) -> float:
    """Значимость новости: дообученная модель (если есть) → иначе формула по весам."""
    predicted = predict_significance(text)
    if predicted is not None:
        return predicted
    s = get_settings()
    return significance_score(
        event_type, score, relevances,
        w_type=s.sig_w_type, w_sent=s.sig_w_sent, w_link=s.sig_w_link,
    )


def _aspect_links(links: list, full_text: str, asset_cache: dict[int, Asset],
                  article_label: str) -> dict[tuple[str, int], tuple[str, bool | None]]:
    """(entity_type, id) → (тональность связи, салиентность) для asset-связей (F1/F2).

    Модели не настроены/упали → копия тональности статьи и NULL-салиентность
    (graceful, поведение до Волны 2). Инференс — только по прямым asset-связям.
    """
    out: dict[tuple[str, int], tuple[str, bool | None]] = {}
    for link in links:
        if link.entity_type != EntityType.ASSET:
            continue
        asset = asset_cache.get(link.entity_id)
        if asset is None:
            continue
        sent, salient = aspect.analyze_pair(
            aspect.aspect_name(asset.ticker, asset.name), full_text
        )
        out[(link.entity_type.value, link.entity_id)] = (sent or article_label, salient)
    return out


# --------------------------------------------------------------------------- #
# Обработчики по типам источников.
# --------------------------------------------------------------------------- #
def _is_duplicate(session: Session, content_hash: str, window_hours: int) -> bool:
    """Есть ли за окно `window_hours` статья с тем же нормализованным хешем заголовка."""
    since = datetime.now(UTC) - timedelta(hours=window_hours)
    return session.scalar(
        select(Article.id).where(
            Article.content_hash == content_hash,
            Article.published_at >= since,
        ).limit(1)
    ) is not None


# F10: какие числа считаем прогнозами брокера (привязываются к активу). Ставку
# (макро, без актива) в этой итерации не храним как forecast — precision-first.
_FORECAST_FACT_KINDS = (numeric.TARGET_PRICE, numeric.DIVIDEND)


def _store_forecasts(session: Session, article_id: int, facts: list,
                     asset_ids: list[int], target_date, channel: str | None) -> int:
    """F10: пишет прогнозные числа (целевая цена/дивиденд) к единственному активу поста.

    Precision-first: привязываем число к активу ТОЛЬКО когда в посте ровно один салиентный
    актив — тогда «целевая цена 420» однозначно про него. Дайджест с несколькими тикерами
    (у каждого своё число) дал бы ложные пары «число × все активы» (нужна аспектная привязка
    числа к объекту — будущая работа), поэтому такие посты пропускаем. Идемпотентно
    (uq_forecast). Возвращает число добавленных строк."""
    if len(asset_ids) != 1:
        return 0
    asset_id = asset_ids[0]
    repo = ForecastRepository(session)
    added = 0
    for fact in facts:
        if fact.kind not in _FORECAST_FACT_KINDS:
            continue
        added += repo.add_forecast(
            article_id=article_id, asset_id=asset_id, kind=fact.kind,
            value=fact.value, unit=fact.unit, target_date=target_date,
            source_channel=channel,
        )
    return added


def _process_news(session: Session, doc: RawDocument, index: EntityIndex,
                  result: ProcessResult, asset_cache: dict[int, Asset],
                  pending_embeddings: list[tuple[Article, str]],
                  degraded: bool = False) -> bool:
    """Обрабатывает новостной raw-документ. Возвращает, помечать ли его processed.

    Б4: при `degraded` (модель тональности/событий/значимости на фолбэке) шумовой скип
    НЕ финализируется (return False) — значимая новость, недооценённая формулой, не
    теряется навсегда, а пересмотрится, когда модель поднимется. Терминальные скипы (нет
    заголовка, дубль) и созданная статья — всегда processed (return True).
    """
    payload = doc.payload or {}
    title = clean_text(payload.get("title"))
    body = clean_text(payload.get("summary"))
    if not title:
        result.skipped += 1
        return True
    full_text = f"{title}. {body}".strip()

    # Дедуп near-duplicate: одна новость от разных лент/источников (косметические отличия
    # текста проходили raw-дедуп) раздувала счётчики neg-spike. Не создаём статью, если за
    # окно `dedup_window_hours` уже есть статья с тем же нормализованным хешем заголовка.
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

    # Фильтр шума: незначимая новость без привязки к активам и категории OTHER/NOISE
    # (спорт/ДТП/культура) не сохраняется. Б4: если модель деградирована (формульный
    # фолбэк), скип НЕ финализируем — иначе значимая новость, недооценённая формулой,
    # теряется навсегда; вернётся на обработку, когда модель поднимется.
    if (significance < settings.min_significance and not links
            and event_type in (EventType.OTHER, EventType.NOISE)):
        if degraded:
            result.deferred += 1
            return False
        result.skipped += 1
        return True

    # F3: временной статус и дата события (модель не настроена → NULL/NULL).
    published = parse_rss_date(payload.get("published"))
    t_status, t_date = temporal.temporal_anchor(
        full_text, (published or datetime.now(UTC)).date()
    )
    # F4: фактологичность (rule-based, усиливается temporal-прогнозом).
    factuality, _ = rumor.classify_factuality(full_text, temporal_status=t_status)
    # F10: прогноз брокера vs новость. Прогноз-статья помечается (не льётся в общий
    # сентимент-грунт) и её числа уходят в forecasts (ниже, после линковки активов).
    is_fc = forecast.is_forecast_post(
        title, body, channel=payload.get("channel"), temporal_status=t_status
    )

    article = Article(
        raw_id=doc.id,
        source=doc.source,
        # F7: канал для telegram (источник тоньше source); RSS — NULL.
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
    session.flush()  # нужен article.id

    # F1/F2 (Волна 2): тональность и салиентность ОТНОСИТЕЛЬНО актива, а не копия
    # тональности статьи (Б2). Модели не настроены → фолбэк на копию (как раньше).
    aspect_by_asset = _aspect_links(links, full_text, asset_cache, label.value)
    salient_asset_ids: list[int] = []  # F10: к каким активам крепить прогноз
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
    # Derived-связи (сектор/страна/тема) — связь новость↔объект не только по тикерам.
    for etype, eid, mention, rel in _extra_entity_rows(session, links, full_text, asset_cache):
        session.add(ArticleEntity(
            article_id=article.id, entity_type=etype, entity_id=eid,
            mention=mention[:256], sentiment=label.value, relevance=rel,
        ))

    # F5: числовые факты текста (дивиденд/ставка/сумма сделки/целевая цена) — rule-based,
    # детерминированно, без модели.
    facts = numeric.extract_numbers(full_text)
    for fact in facts:
        session.add(ArticleNumber(
            article_id=article.id, kind=fact.kind, value=fact.value,
            unit=fact.unit, snippet=fact.snippet,
        ))
    # F10: для прогноз-постов привязываем целевую цену/дивиденд к салиентным активам.
    if is_fc:
        _store_forecasts(session, article.id, facts, salient_asset_ids,
                         t_date, payload.get("channel"))

    # Эмбеддинг считаем не здесь, а одним батчем после цикла (см. process_pending) — на CPU
    # это 5–10× быстрее, чем по одной статье. article.id уже валиден (был flush выше).
    pending_embeddings.append((article, full_text))

    result.articles += 1
    return True


def _pipeline_degraded() -> bool:
    """Б4: True, если модель тональности/событий/значимости работает на фолбэке.

    Только настоящая деградация (адаптер настроен, но не загрузился) — не штатный
    формульный/лексиконный режим без адаптера. Модели @lru_cache → проверка дешёвая,
    зовётся раз на батч. Сбой самой проверки трактуем как деградацию (осторожно).
    """
    from geoanalytics.nlp import classify, sentiment, significance

    for status_fn in (sentiment.model_status, classify.model_status,
                      significance.model_status):
        try:
            status, _ = status_fn()
        except Exception:  # noqa: BLE001 — проверка не должна валить конвейер
            return True
        if status != "ok":
            return True
    return False


def _process_market(session: Session, doc: RawDocument, result: ProcessResult) -> None:
    p = doc.payload or {}
    ticker = p.get("ticker")
    if not ticker:
        result.skipped += 1
        return

    # upsert актива (могли не сидировать — создаём на лету).
    asset = session.scalars(select(Asset).where(Asset.ticker == ticker)).first()
    if asset is None:
        asset = Asset(ticker=ticker, name=p.get("name") or ticker, isin=p.get("isin"),
                      kind="share", board=p.get("board"))
        session.add(asset)
        session.flush()
        # Б9: актив без company_id выпадает из секторных скоупов и линковки —
        # громко, чтобы эмитента добавили в seed.ISSUERS (health тоже следит).
        log.warning("asset_created_without_company", ticker=ticker)
    elif p.get("isin") and not asset.isin:
        asset.isin = p.get("isin")

    last = _to_float(p.get("last"))
    ts = parse_moex_systime(p.get("updated"))
    if last is None or ts is None:
        result.skipped += 1  # неликвид/нет котировки на момент среза
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
            # unit задаёт коннектор (металлы — RUB/g); исторический дефолт — "%".
            .values(indicator=p.get("indicator"), ts=ts, value=value,
                    unit=p.get("unit") or "%")
            .on_conflict_do_nothing(constraint="uq_macro_point")
        )
        if session.execute(stmt).rowcount:
            result.macro += 1
    else:
        result.skipped += 1


@dataclass
class RelinkResult:
    """Итог перелинковки уже сохранённых статей."""

    articles: int = 0
    links: int = 0
    embeddings: int = 0


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
        for art in articles:
            result.articles += 1
            full_text = f"{art.title}. {art.text or ''}".strip()
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
            # Derived-связи (сектор/страна/тема) — идемпотентно.
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
            # Пересчитываем значимость по освежённым связям (линковка улучшилась).
            art.significance = _compute_significance(
                art.event_type, art.sentiment_score,
                [link.relevance for link in links], full_text,
            )
            if embedder is not None and art.id not in have_embedding:
                to_embed.append((art.id, full_text))
        # Эмбеддинги — одним батчем после цикла (5–10× быстрее на CPU), с fallback на одиночные.
        result.embeddings = _embed_batch(session, embedder, to_embed)
        # Связи изменились → привести EventImpact в соответствие (настоящий фикс мины
        # устаревших импактов, model-data-errors #1): удалить призраков, освежить знаки.
        from geoanalytics.context.events import reconcile_impacts
        reconcile_impacts(session, article_ids=[a.id for a in articles])
    log.info("relink_done", articles=result.articles, links=result.links,
             embeddings=result.embeddings)
    return result


# --------------------------------------------------------------------------- #
# Переразметка уже сохранённых статей обновлёнными моделями (без пересоздания).
# --------------------------------------------------------------------------- #
# Стадии переразметки. Значимость — производная (зависит от тональности, типа события и
# связей), поэтому пересчитывается, если меняется любая вышестоящая стадия, и доступна
# как отдельная стадия (например, при изменении весов формулы GEO_SIG_W_*).
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
    # Сдвиг распределения тональности (до → после) — для отчёта и проверки эффекта.
    sentiment_before: Counter = field(default_factory=Counter)
    sentiment_after: Counter = field(default_factory=Counter)


def _rescore_article(session: Session, art: Article, relevances: list[float], *,
                     stages: tuple[str, ...], do_significance: bool,
                     result: RescoreResult, dry_run: bool) -> None:
    """Пересчитывает выбранные модельные поля одной статьи.

    Связи не пересоздаются (для этого `relink_existing`): значимость считается по уже
    сохранённым релевантностям. Изменения применяются только при `dry_run=False`.
    """
    result.articles += 1
    full_text = f"{art.title}. {art.text or ''}".strip()

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
    offset = 0
    while limit is None or result.articles < limit:
        take = batch_size if limit is None else min(batch_size, limit - result.articles)
        with session_scope() as session:
            articles = list(session.scalars(
                select(Article).order_by(Article.id).offset(offset).limit(take)
            ))
            if not articles:
                break
            # Релевантности связей для всего батча одним запросом (для значимости).
            ids = [a.id for a in articles]
            rel_map: dict[int, list[float]] = defaultdict(list)
            for aid, rel in session.execute(
                select(ArticleEntity.article_id, ArticleEntity.relevance)
                .where(ArticleEntity.article_id.in_(ids))
            ).all():
                rel_map[aid].append(rel or 0.0)
            for art in articles:
                try:
                    with session.begin_nested():  # SAVEPOINT на статью
                        _rescore_article(
                            session, art, rel_map.get(art.id, []),
                            stages=stages, do_significance=do_significance,
                            result=result, dry_run=dry_run,
                        )
                except Exception as exc:  # noqa: BLE001 — одна статья не валит батч
                    result.errors += 1
                    log.error("rescore_article_failed", article_id=art.id, error=str(exc))
        offset += len(articles)
        if len(articles) < take:
            break
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
    """Переразмечает СУЩЕСТВУЮЩИЕ связи статья↔актив aspect-моделями (F1/F2).

    Нужна после деплоя/смены aspect-моделей: исторические связи несут копию
    тональности статьи (Б2). Идемпотентно (модели детерминированы). Без моделей —
    no-op (нечем размечать).
    """
    result = ReaspectResult()
    if aspect._get_sentiment_model() is None and aspect._get_saliency_model() is None:
        log.warning("reaspect_no_models")
        return result
    offset = 0
    while limit is None or result.links < limit:
        take = batch_size if limit is None else min(batch_size, limit - result.links)
        with session_scope() as session:
            rows = session.execute(
                select(ArticleEntity, Article.title, Article.text, Asset.ticker, Asset.name)
                .join(Article, Article.id == ArticleEntity.article_id)
                .join(Asset, Asset.id == ArticleEntity.entity_id)
                .where(ArticleEntity.entity_type == EntityType.ASSET.value)
                .order_by(ArticleEntity.id)
                .offset(offset).limit(take)
            ).all()
            if not rows:
                break
            for link, title, body, ticker, name in rows:
                result.links += 1
                try:
                    full_text = f"{title}. {body or ''}".strip()
                    sent, salient = aspect.analyze_pair(
                        aspect.aspect_name(ticker, name), full_text
                    )
                    if sent is not None and sent != link.sentiment:
                        link.sentiment = sent
                        result.sentiment_changed += 1
                    if salient is not None and salient != link.salient:
                        link.salient = salient
                        result.salient_set += 1
                except Exception as exc:  # noqa: BLE001 — одна связь не валит батч
                    result.errors += 1
                    log.error("reaspect_failed", link_id=link.id, error=str(exc))
        offset += len(rows)
        if len(rows) < take:
            break
    # Тональность/salient связей изменились → переотразить во всех импактах и убрать
    # призраков (model-data-errors #1). reaspect меняет связи глобально → полная сверка.
    if result.sentiment_changed or result.salient_set:
        from geoanalytics.context.events import reconcile_impacts
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
    """Размечает СУЩЕСТВУЮЩИЕ статьи temporal-моделью (F3): статус + дата события.

    Нужна после деплоя/смены temporal-модели. Идемпотентно (модель и экстрактор
    детерминированы). Без модели — no-op.
    """
    result = RetemporalResult()
    if temporal._model() is None:
        log.warning("retemporal_no_model")
        return result
    offset = 0
    while limit is None or result.articles < limit:
        take = batch_size if limit is None else min(batch_size, limit - result.articles)
        with session_scope() as session:
            rows = session.scalars(
                select(Article).order_by(Article.id).offset(offset).limit(take)
            ).all()
            if not rows:
                break
            for art in rows:
                result.articles += 1
                try:
                    full_text = f"{art.title}. {art.text or ''}".strip()
                    published = (art.published_at or datetime.now(UTC)).date()
                    status, ev_date = temporal.temporal_anchor(full_text, published)
                    if status is not None and status != art.temporal_status:
                        art.temporal_status = status
                        result.status_set += 1
                    if ev_date is not None and ev_date != art.event_date:
                        art.event_date = ev_date
                        result.date_set += 1
                except Exception as exc:  # noqa: BLE001 — одна статья не валит батч
                    result.errors += 1
                    log.error("retemporal_failed", article_id=art.id, error=str(exc))
        offset += len(rows)
        if len(rows) < take:
            break
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
    """Размечает СУЩЕСТВУЮЩИЕ статьи фактологичностью F4 (fact/rumor/opinion).

    Нужна после деплоя/изменения правил nlp/rumor.py. Детерминирована и идемпотентна;
    усиливается уже проставленным temporal_status (прогноз → rumor).
    """
    result = RefactualityResult()
    offset = 0
    while limit is None or result.articles < limit:
        take = batch_size if limit is None else min(batch_size, limit - result.articles)
        with session_scope() as session:
            rows = session.scalars(
                select(Article).order_by(Article.id).offset(offset).limit(take)
            ).all()
            if not rows:
                break
            for art in rows:
                result.articles += 1
                try:
                    full_text = f"{art.title}. {art.text or ''}".strip()
                    label, _ = rumor.classify_factuality(
                        full_text, temporal_status=art.temporal_status
                    )
                    if label != art.factuality:
                        art.factuality = label
                        result.set_count += 1
                    result.by_label[label] = result.by_label.get(label, 0) + 1
                except Exception as exc:  # noqa: BLE001 — одна статья не валит батч
                    result.errors += 1
                    log.error("refactuality_failed", article_id=art.id, error=str(exc))
        offset += len(rows)
        if len(rows) < take:
            break
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
    """Извлекает числовые факты (F5) из СУЩЕСТВУЮЩИХ статей.

    Нужна после деплоя/изменения правил nlp/numeric.py. Идемпотентно:
    upsert с ON CONFLICT DO NOTHING по uq_artnum.
    """
    result = RenumericResult()
    offset = 0
    while limit is None or result.articles < limit:
        take = batch_size if limit is None else min(batch_size, limit - result.articles)
        with session_scope() as session:
            rows = session.execute(
                select(Article.id, Article.title, Article.text)
                .order_by(Article.id).offset(offset).limit(take)
            ).all()
            if not rows:
                break
            for art_id, title, body in rows:
                result.articles += 1
                try:
                    facts = numeric.extract_numbers(f"{title}. {body or ''}".strip())
                except Exception as exc:  # noqa: BLE001 — одна статья не валит батч
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
        offset += len(rows)
        if len(rows) < take:
            break
    log.info("renumeric_done", articles=result.articles, facts=result.facts,
             by_kind=result.by_kind, errors=result.errors)
    return result


@dataclass
class ReforecastResult:
    articles: int = 0   # просмотрено брокерских статей
    marked: int = 0     # помечено is_forecast
    forecasts: int = 0  # добавлено строк в forecasts
    errors: int = 0


def reforecast_existing(limit: int | None = None,
                        batch_size: int = 500) -> ReforecastResult:
    """F10: размечает СУЩЕСТВУЮЩИЕ брокерские статьи — is_forecast + наполнение forecasts.

    Нужна после деплоя роутера/правил target_price. Идемпотентно (uq_forecast,
    повторная пометка no-op). Ходит только по статьям брокерских каналов (source_ref)."""
    result = ReforecastResult()
    channels = list(forecast.BROKER_CHANNELS)
    offset = 0
    while limit is None or result.articles < limit:
        take = batch_size if limit is None else min(batch_size, limit - result.articles)
        with session_scope() as session:
            arts = session.scalars(
                select(Article).where(Article.source_ref.in_(channels))
                .order_by(Article.id).offset(offset).limit(take)
            ).all()
            if not arts:
                break
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
                    text = f"{art.title}. {art.text or ''}".strip()
                    result.forecasts += _store_forecasts(
                        session, art.id, numeric.extract_numbers(text),
                        asset_ids, art.event_date, art.source_ref,
                    )
                except Exception as exc:  # noqa: BLE001 — одна статья не валит батч
                    result.errors += 1
                    log.error("reforecast_failed", article_id=art.id, error=str(exc))
        offset += len(arts)
        if len(arts) < take:
            break
    log.info("reforecast_done", articles=result.articles, marked=result.marked,
             forecasts=result.forecasts, errors=result.errors)
    return result


def _embed_batch(session: Session, embedder, items: list[tuple[int, str]]) -> int:
    """Считает эмбеддинги накопленного батча одним вызовом и добавляет Embedding-строки.

    Робастность: если батч-вызов упал (битый текст и т.п.) — откатываемся на пер-статейный
    `embed_one`, чтобы не потерять эмбеддинги всего батча из-за одного входа. Возвращает
    число добавленных строк. `items` — список (article_id, full_text)."""
    if embedder is None or not items:
        return 0
    texts = [t for _, t in items]
    try:
        vectors: list = embedder.embed(texts)
    except Exception as exc:  # noqa: BLE001 — деградируем до пер-статейного пути
        log.warning("embed_batch_failed_fallback", count=len(texts), error=str(exc))
        vectors = []
        for t in texts:
            try:
                vectors.append(embedder.embed_one(t))
            except Exception as e2:  # noqa: BLE001 — единичный битый текст пропускаем
                log.warning("embed_one_failed", error=str(e2))
                vectors.append(None)
    added = 0
    for (aid, _), vec in zip(items, vectors, strict=True):
        if vec is None:
            continue
        session.add(Embedding(article_id=aid, model=embedder.model_name, vector=vec))
        added += 1
    return added


def process_pending(batch_size: int = 500) -> ProcessResult:
    """Обрабатывает накопленные необработанные raw-документы."""
    result = ProcessResult()
    with session_scope() as session:
        index = EntityIndex(session)  # один индекс на батч
        asset_cache = _load_asset_cache(session)  # один запрос на батч (убирает N+1 в линковке)
        embedder = get_embedder()
        # Б4: деградацию моделей проверяем раз на батч — шумовой скип при фолбэке не
        # финализируем (вернётся, когда модель поднимется).
        degraded = _pipeline_degraded()
        if degraded:
            log.warning("processing_models_degraded_defer_noise_skips")
        pending_embeddings: list[tuple[Article, str]] = []  # копим тексты на батч-эмбеддинг
        stmt = (
            select(RawDocument)
            .where(RawDocument.processed.is_(False))
            .order_by(RawDocument.fetched_at)
            .limit(batch_size)
        )
        docs = list(session.scalars(stmt))
        for doc in docs:
            kind = _source_kind(doc.source)
            embed_mark = len(pending_embeddings)  # для отката при ошибке документа
            try:
                # SAVEPOINT на документ: ошибка откатывает только его, не весь батч.
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
            except Exception as exc:  # noqa: BLE001 — один документ не валит весь батч
                # Откатился SAVEPOINT — выкидываем и накопленные для него эмбеддинги
                # (иначе вставка Embedding сослалась бы на несуществующую статью).
                del pending_embeddings[embed_mark:]
                result.errors += 1
                log.error("process_doc_failed", doc_id=doc.id, source=doc.source, error=str(exc))
        # Эмбеддинги — одним батчем после цикла (статьи уже зафиксированы, id валидны).
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
    """Б4 follow-up: переоткрыть новостные raw-доки, помеченные processed, но НЕ давшие статью.

    Скип по шуму/деградации модели/дедупу ставит processed=True — после апгрейда моделей или
    понижения порога значимости такие новости иначе не вернулись бы. Здесь снимаем флаг → их
    подхватит `process_pending` (ближайший цикл scheduler или `geo process`). Безопасно: дедуп
    БД и шумовой фильтр снова отсеют настоящие дубли/мусор. Ограничено NEWS-источниками
    (market/macro-скипы терминальны — payload неизменен). Возвращает число переоткрытых.
    """
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
