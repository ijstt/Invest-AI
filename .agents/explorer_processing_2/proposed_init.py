"""Пакет обработки данных: raw-слой -> нормализованные сущности.

Обеспечивает конвейерную обработку новых документов и переразметку существующих.
"""

# Экспортируем приватные функции, импортируемые в тестах (tests/test_processing.py),
# для соблюдения обратной совместимости API.
from geoanalytics.processing.common import (
    _embed_batch,
    _extra_entity_rows,
    _pipeline_degraded,
)
from geoanalytics.processing.pipeline import (
    ProcessResult,
    ReprocessResult,
    _process_news,
    process_pending,
    reprocess_skipped,
)
from geoanalytics.processing.reprocessing import (
    RESCORE_STAGES,
    ReaspectResult,
    RefactualityResult,
    ReforecastResult,
    RelinkResult,
    RenumericResult,
    RescoreResult,
    RetemporalResult,
    _rescore_article,
    reaspect_existing,
    refactuality_existing,
    reforecast_existing,
    relink_existing,
    renumeric_existing,
    rescore_existing,
    retemporal_existing,
)

__all__ = [
    "ProcessResult",
    "ReprocessResult",
    "process_pending",
    "reprocess_skipped",
    "ReaspectResult",
    "RefactualityResult",
    "ReforecastResult",
    "RelinkResult",
    "RenumericResult",
    "RescoreResult",
    "RetemporalResult",
    "RESCORE_STAGES",
    "reaspect_existing",
    "refactuality_existing",
    "reforecast_existing",
    "relink_existing",
    "renumeric_existing",
    "rescore_existing",
    "retemporal_existing",
    # Совместимость с тестами
    "_embed_batch",
    "_extra_entity_rows",
    "_pipeline_degraded",
    "_process_news",
    "_rescore_article",
]
