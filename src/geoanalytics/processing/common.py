from __future__ import annotations

import contextlib
from collections import Counter
from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from config.settings import get_settings
from geoanalytics.connectors.registry import get_connector
from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import EntityType, SourceKind
from geoanalytics.nlp import (
    aspect,
    classify,
    numeric,
    sentiment,
)
from geoanalytics.nlp.significance import significance_score
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import (
    Article,
    Asset,
    Embedding,
    MacroTheme,
)
from geoanalytics.storage.repositories import ForecastRepository

log = get_logger("processing")


def paginate_query[T](
    fetch_fn: Callable[[Session, int, int], list[T]],
    batch_size: int,
    limit: int | None = None,
) -> Generator[tuple[Session, list[T]], None, None]:
    """Generically paginates query execution over database sessions."""
    offset = 0
    total_processed = 0
    while limit is None or total_processed < limit:
        take = batch_size if limit is None else min(batch_size, limit - total_processed)
        with session_scope() as session:
            batch = fetch_fn(session, offset, take)
            if not batch:
                break
            try:
                yield session, batch
            except BaseException:
                session.rollback()
                raise
            offset += len(batch)
            total_processed += len(batch)
            if len(batch) < take:
                break


def make_full_text(title: str | None, body: str | None) -> str:
    """Constructs clean full text from title and body/text components."""
    title_clean = title.strip() if title else ""
    body_clean = body.rstrip() if body else ""

    if not title_clean:
        return body_clean.lstrip()
    if not body_clean:
        if title_clean.endswith("."):
            return title_clean
        return title_clean + "."

    if body_clean.startswith(" "):
        return f"{title_clean.rstrip('.')}.{body_clean}"
    return f"{title_clean.rstrip('.')}. {body_clean}"


def _load_asset_cache(session: Session) -> dict[int, Asset]:
    """Все активы с предзагруженными компаниями — один запрос на батч. Активов мало
    (эмитенты), поэтому грузим целиком и убираем N+1 из горячего цикла `_extra_entity_rows`."""
    assets = session.scalars(select(Asset).options(selectinload(Asset.company)))
    return {a.id: a for a in assets}


def _extra_entity_rows(
    session: Session, links: list, full_text: str, asset_cache: dict[int, Asset]
) -> list[tuple]:
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
        # derived-связь слабее прямой (×0.8): thematic, не прямое упоминание.
        for etype, eid in (("sector", comp.sector_id), ("country", comp.country_id)):
            if eid and (etype, eid) not in seen:
                seen.add((etype, eid))
                extra.append((etype, eid, asset.ticker, round(link.relevance * 0.8, 3)))

    import geoanalytics.processing as gp

    tnames = gp.classify_themes(full_text)
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
    deferred: int = 0  # Б4: шумовой скип отложен (модель деградирована) — не помечен processed
    errors: int = 0
    by_source: dict[str, int] = field(default_factory=dict)


@dataclass
class RelinkResult:
    """Итог перелинковки уже сохранённых статей."""

    articles: int = 0
    links: int = 0
    embeddings: int = 0


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


@dataclass
class ReaspectResult:
    """Итог переразметки asset-связей aspect-моделями (F1/F2)."""

    links: int = 0
    sentiment_changed: int = 0
    salient_set: int = 0
    errors: int = 0


@dataclass
class RetemporalResult:
    articles: int = 0
    status_set: int = 0
    date_set: int = 0
    errors: int = 0


@dataclass
class RefactualityResult:
    articles: int = 0
    set_count: int = 0
    by_label: dict[str, int] = field(default_factory=dict)
    errors: int = 0


@dataclass
class RenumericResult:
    articles: int = 0
    facts: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    errors: int = 0


@dataclass
class ReforecastResult:
    articles: int = 0  # просмотрено брокерских статей
    marked: int = 0  # помечено is_forecast
    forecasts: int = 0  # добавлено строк в forecasts
    errors: int = 0


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


def _compute_significance(
    event_type: str, score: float | None, relevances: list[float], text: str
) -> float:
    """Значимость новости: дообученная модель (если есть) → иначе формула по весам."""
    import geoanalytics.processing as gp

    predicted = gp.predict_significance(text)
    if predicted is not None:
        return predicted
    s = get_settings()
    return significance_score(
        event_type,
        score,
        relevances,
        w_type=s.sig_w_type,
        w_sent=s.sig_w_sent,
        w_link=s.sig_w_link,
    )


def _aspect_links(
    links: list, full_text: str, asset_cache: dict[int, Asset], article_label: str
) -> dict[tuple[str, int], tuple[str, bool | None]]:
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
        sent, salient = aspect.analyze_pair(aspect.aspect_name(asset.ticker, asset.name), full_text)
        out[(link.entity_type.value, link.entity_id)] = (sent or article_label, salient)
    return out


def _is_duplicate(session: Session, content_hash: str, window_hours: int) -> bool:
    """Есть ли за окно `window_hours` статья с тем же нормализованным хешем заголовка."""
    since = datetime.now(UTC) - timedelta(hours=window_hours)
    return (
        session.scalar(
            select(Article.id)
            .where(
                Article.content_hash == content_hash,
                Article.published_at >= since,
            )
            .limit(1)
        )
        is not None
    )


# F10: какие числа считаем прогнозами брокера (привязываются к активу). Ставку
# (макро, без актива) в этой итерации не храним как forecast — precision-first.
_FORECAST_FACT_KINDS = (numeric.TARGET_PRICE, numeric.DIVIDEND)


def _store_forecasts(
    session: Session,
    article_id: int,
    facts: list,
    asset_ids: list[int],
    target_date,
    channel: str | None,
) -> int:
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
            article_id=article_id,
            asset_id=asset_id,
            kind=fact.kind,
            value=fact.value,
            unit=fact.unit,
            target_date=target_date,
            source_channel=channel,
        )
    return added


def _pipeline_degraded() -> bool:
    """Б4: True, если модель тональности/событий/значимости работает на фолбэке.

    Только настоящая деградация (адаптер настроен, но не загрузился) — не штатный
    формульный/лексиконный режим без адаптера. Модели @lru_cache → проверка дешёвая,
    зовётся раз на батч. Сбой самой проверки трактуем как деградацию (осторожно).
    """
    from geoanalytics.nlp import significance

    for status_fn in (sentiment.model_status, classify.model_status, significance.model_status):
        try:
            status, _ = status_fn()
        except Exception:  # noqa: BLE001 — проверка не должна валить конвейер
            return True
        if status != "ok":
            return True
    return False


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
        if len(vectors) != len(items):
            raise ValueError(f"Embedder returned {len(vectors)} vectors, expected {len(items)}")
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


def build_article_text(
    article_or_title: object | str | None = None, text: str | None = None
) -> str:
    """Helper to construct clean full text from Article model, duck-typed stub, or string params."""
    if (
        article_or_title is not None
        and not isinstance(article_or_title, str)
        and hasattr(article_or_title, "title")
    ):
        title = getattr(article_or_title, "title", None)
        body = getattr(article_or_title, "text", None)
        if body is None:
            body = getattr(article_or_title, "body", None)
        return make_full_text(title, body)

    title_str = (
        article_or_title
        if (isinstance(article_or_title, str) or article_or_title is None)
        else None
    )
    return make_full_text(title_str, text)


def execute_reprocessing[T](
    session: Session,
    items: Iterable[T],
    process_item_fn: Callable[[Session, T], None],
    *,
    use_savepoint: bool = True,
    log_error_name: str = "reprocessing_item_failed",
    error_extra_fn: Callable[[T], dict] | None = None,
) -> int:
    """Generically drives batch/item processing with transaction savepoints (using
    session.begin_nested() or contextlib.nullcontext()), item-level exception handling,
    and error logging. Returns the count of encountered errors.
    """
    errors_count = 0
    for item in items:
        try:
            if use_savepoint:
                context = session.begin_nested()
            else:
                context = contextlib.nullcontext()

            with context:
                process_item_fn(session, item)
        except Exception as exc:
            errors_count += 1
            extra = error_extra_fn(item) if error_extra_fn else {}
            log.error(log_error_name, error=str(exc), **extra)
    return errors_count
