"""Сервис ингеста: запускает коннектор(ы) и складывает RawItem в raw-слой.

Дедупликация — на уровне БД (uq_raw_doc_hash), поэтому повторный запуск
не плодит дубликаты.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from geoanalytics.connectors.base import RawItem
from geoanalytics.connectors.registry import all_connectors, get_connector
from geoanalytics.core.logging import get_logger
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.repositories import RawRepository

log = get_logger("ingest")


@dataclass
class IngestResult:
    """Итог ингеста одного источника."""

    source: str
    fetched: int = 0
    stored: int = 0      # реально новых (не дубли)
    errors: int = 0


def ingest_source(name: str) -> IngestResult:
    """Запускает один источник и сохраняет новые документы."""
    connector = get_connector(name)
    result = IngestResult(source=name)
    with session_scope() as session:
        repo = RawRepository(session)
        try:
            for item in connector.fetch():
                result.fetched += 1
                doc = repo.add_if_new(
                    source=item.source,
                    raw_text=item.raw_text,
                    external_id=item.external_id,
                    payload=item.payload,
                )
                if doc is not None:
                    result.stored += 1
        except Exception as exc:  # noqa: BLE001 — не валим весь ингест из-за одного источника
            result.errors += 1
            log.error("ingest_failed", source=name, error=str(exc))
    log.info("ingest_done", source=name, fetched=result.fetched, stored=result.stored)
    return result


def store_items(items: Iterable[RawItem], source: str) -> IngestResult:
    """Сохраняет уже собранный поток RawItem в raw-слой (дедуп на уровне БД).

    Для путей, которые получают элементы вне `connector.fetch()` — напр. историч.
    бэкфилл новостей (geo news-backfill). Дубликаты тихо пропускаются (uq_raw_doc_hash)."""
    result = IngestResult(source=source)
    with session_scope() as session:
        repo = RawRepository(session)
        for item in items:
            result.fetched += 1
            doc = repo.add_if_new(
                source=item.source,
                raw_text=item.raw_text,
                external_id=item.external_id,
                payload=item.payload,
            )
            if doc is not None:
                result.stored += 1
    log.info("ingest_stored", source=source, fetched=result.fetched, stored=result.stored)
    return result


def ingest_all() -> list[IngestResult]:
    """Запускает все зарегистрированные источники."""
    return [ingest_source(c.name) for c in all_connectors()]
