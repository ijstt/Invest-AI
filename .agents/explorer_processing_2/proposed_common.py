"""Вспомогательные утилиты и общий итератор для конвейера обработки."""

from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any, TypeVar

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
from geoanalytics.nlp import (
    significance as nlp_significance,
)
from geoanalytics.nlp.significance import predict_significance, significance_score
from geoanalytics.nlp.themes import classify_themes
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import (
    Asset,
    Embedding,
    MacroTheme,
)
from geoanalytics.storage.repositories import ForecastRepository

log = get_logger("processing")

T = TypeVar("T")


def build_full_text(title: str | None, text_or_body: str | None) -> str:
    """Объединяет заголовок и тело новости в единую строку full_text,

    обрабатывая возможные значения None.
    """
    return f"{title or ''}. {text_or_body or ''}".strip()


def paginate_query(
    query_fn: Callable[[Session], Any],
    *,
    batch_size: int = 500,
    limit: int | None = None,
    scalar: bool = False,
) -> Generator[tuple[Session, list[Any]], None, None]:
    """Универсальный итератор для батчевой пагинации запросов в БД с offset/limit.

    Инициализирует транзакционный session_scope на каждый батч.
    """
    offset = 0
    processed = 0
    while limit is None or processed < limit:
        take = batch_size if limit is None else min(batch_size, limit - processed)
        with session_scope() as session:
            stmt = query_fn(session).offset(offset).limit(take)
            if scalar:
                items = list(session.scalars(stmt).all())
            else:
                items = session.execute(stmt).all()

            if not items:
                break

            yield session, items

            count = len(items)
            offset += count
            processed += count
            if count < take:
                break


def _load_asset_cache(session: Session) -> dict[int, Asset]:
    """Все активы с предзагруженными компаниями — один запрос на батч. Активов мало

    (эмитенты), поэтому грузим целиком и убираем N+1 из горячего цикла `_extra_entity_rows`."""
    assets = session.scalars(select(Asset).options(selectinload(Asset.company)))
    return {a.id: a for a in assets}


def _extra_entity_rows(session: Session, links: list, full_text: str,
                       asset_cache: dict[int, Asset]) -> list[tuple]:
    """Доп. связи новости сверх прямых упоминаний: derived сектор/страна (через компанию

    актива) и макро-темы (по ключевым словам). Возвращает (entity_type, id, mention, rel).
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
    """(entity_type, id) → (тональность связи, салиентность) для asset-связей (F1/F2)."""
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


_FORECAST_FACT_KINDS = (numeric.TARGET_PRICE, numeric.DIVIDEND)


def _store_forecasts(session: Session, article_id: int, facts: list,
                     asset_ids: list[int], target_date, channel: str | None) -> int:
    """F10: пишет прогнозные числа (целевая цена/дивиденд) к единственному активу поста."""
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


def _embed_batch(session: Session, embedder, items: list[tuple[int, str]]) -> int:
    """Считает эмбеддинги накопленного батча одним вызовом и добавляет Embedding-строки."""
    if embedder is None or not items:
        return 0
    texts = [t for _, t in items]
    try:
        vectors: list = embedder.embed(texts)
    except Exception as exc:  # noqa: BLE001
        log.warning("embed_batch_failed_fallback", count=len(texts), error=str(exc))
        vectors = []
        for t in texts:
            try:
                vectors.append(embedder.embed_one(t))
            except Exception as e2:  # noqa: BLE001
                log.warning("embed_one_failed", error=str(e2))
                vectors.append(None)
    added = 0
    for (aid, _), vec in zip(items, vectors, strict=True):
        if vec is None:
            continue
        session.add(Embedding(article_id=aid, model=embedder.model_name, vector=vec))
        added += 1
    return added


def _pipeline_degraded() -> bool:
    """Б4: True, если модель тональности/событий/значимости работает на фолбэке."""
    for status_fn in (sentiment.model_status, classify.model_status,
                      nlp_significance.model_status):
        try:
            status, _ = status_fn()
        except Exception:  # noqa: BLE001
            return True
        if status != "ok":
            return True
    return False
