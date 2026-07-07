"""Коннектор ставок ЕЦБ через ECB Data Portal API.

Тянем ключевые ставки ЕЦБ: ставку по депозитам (DFR — основной инструмент
политики последних лет) и ставку основных операций рефинансирования (MRR).
Это внешний макро-фактор (стоимость евро, глобальные ставки). Пишем как
макро-серии (`macro_series`).

Используем CSV-формат API (проще и устойчивее SDMX-JSON). Ключ не требуется.
Документация: https://data.ecb.europa.eu/help/api/overview
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from datetime import datetime

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from geoanalytics.connectors.base import BaseConnector, RawItem
from geoanalytics.connectors.registry import register
from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import SourceKind

log = get_logger("connector.ecb")

DATA_URL = "https://data-api.ecb.europa.eu/service/data/FM/{key}"
# Сопоставление серий ЕЦБ (ключ в наборе FM) → имя индикатора в macro_series.
SERIES = {
    "D.U2.EUR.4F.KR.DFR.LEV": "ecb_dfr",       # ставка по депозитам
    "D.U2.EUR.4F.KR.MRR_FR.LEV": "ecb_mrr",     # основные операции рефинансирования
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def _get_csv(key: str) -> str:
    resp = httpx.get(
        DATA_URL.format(key=key),
        params={"format": "csvdata", "lastNObservations": 1},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.text


def _latest_from_csv(text: str) -> tuple[str, float] | None:
    """Последнее наблюдение из CSV ЕЦБ: (дата ISO, значение)."""
    reader = csv.DictReader(io.StringIO(text))
    last = None
    for row in reader:
        period = row.get("TIME_PERIOD")
        raw = row.get("OBS_VALUE")
        if not period or raw in (None, ""):
            continue
        try:
            last = (period, float(raw))
        except ValueError:
            continue
    return last


@register
class EcbConnector(BaseConnector):
    name = "ecb"
    kind = SourceKind.MACRO

    def fetch(self) -> Iterable[RawItem]:
        for key, indicator in SERIES.items():
            try:
                observation = _latest_from_csv(_get_csv(key))
            except Exception as exc:  # noqa: BLE001 — один ряд не валит остальные
                log.warning("ecb_series_failed", series=key, error=str(exc))
                continue
            if observation is None:
                log.warning("ecb_no_data", series=key)
                continue
            iso_date, value = observation
            # processing._process_macro ожидает дату в формате dd.mm.yyyy (как ЦБ).
            date = datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d.%m.%Y")
            log.info("ecb_value", indicator=indicator, value=value, date=date)
            yield RawItem(
                source=self.name,
                external_id=f"{indicator}:{date}",
                raw_text=f"ECB {indicator}={value} on {date}",
                payload={"kind": "macro", "indicator": indicator, "value": value, "date": date},
            )
