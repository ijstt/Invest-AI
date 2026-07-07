"""Статус-фид пайплайна (Волна 6в): прозрачность «что происходит внутри».

Эвристика свежести по СЛЕДАМ в БД (что планировщик реально записал), а не по логам — так
дашборд видит состояние из своего процесса, не завися от in-memory heartbeat планировщика
(он в другом процессе). Чистый раннер: сессия → словарь для панели.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from geoanalytics.storage.models import AlertRecord, Article, RawDocument

# Свежесть ингеста: позже этого порога без новых документов — пайплайн считаем «отстал».
_STALE_AFTER = timedelta(hours=2)


def _ago(ts: datetime | None, now: datetime) -> str | None:
    """Человекочитаемое «N мин/ч/дн назад» (None — нет данных)."""
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    sec = (now - ts).total_seconds()
    if sec < 90:
        return "только что"
    if sec < 3600:
        return f"{int(sec // 60)} мин назад"
    if sec < 86400:
        return f"{int(sec // 3600)} ч назад"
    return f"{int(sec // 86400)} дн назад"


def pipeline_status(session: Session) -> dict:
    """Срез состояния пайплайна: свежесть ингеста, объём за сутки, бэклог обработки, последний
    алерт/статья. `fresh` — ингест в пределах порога (иначе панель подсветит «отстал»)."""
    now = datetime.now(UTC)
    since = now - timedelta(hours=24)

    last_ingest = session.scalar(select(func.max(RawDocument.fetched_at)))
    docs_24h = session.scalar(
        select(func.count()).select_from(RawDocument).where(RawDocument.fetched_at >= since)
    ) or 0
    unprocessed = session.scalar(
        select(func.count()).select_from(RawDocument).where(RawDocument.processed.is_(False))
    ) or 0
    last_alert = session.scalar(select(func.max(AlertRecord.created_at)))
    last_article = session.scalar(select(func.max(Article.published_at)))

    fresh = last_ingest is not None and (
        now - (last_ingest if last_ingest.tzinfo else last_ingest.replace(tzinfo=UTC))
    ) <= _STALE_AFTER
    return {
        "fresh": fresh,
        "last_ingest": _ago(last_ingest, now),
        "docs_24h": int(docs_24h),
        "unprocessed": int(unprocessed),
        "last_alert": _ago(last_alert, now),
        "last_article": _ago(last_article, now),
    }
