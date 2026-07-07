"""Коннектор макропоказателей ФРС США через FRED API (St. Louis Fed).

Тянем ставку ФРС (FEDFUNDS) и доходность 10-летних трежерис (DGS10) — ключевые
внешние факторы для рынка РФ (стоимость доллара, риск-аппетит, нефть). Пишем как
макро-серии (`macro_series`).

Требуется бесплатный API-ключ (`GEO_FRED_API_KEY`). Без ключа источник тихо
пропускается — graceful degradation, как и у остальных опциональных компонентов.
Документация: https://fred.stlouisfed.org/docs/api/fred/
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import get_settings
from geoanalytics.connectors.base import BaseConnector, RawItem
from geoanalytics.connectors.registry import register
from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import SourceKind

log = get_logger("connector.fred")

OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
# Сопоставление серий FRED → имя индикатора в нашей macro_series.
SERIES = {
    "FEDFUNDS": "fed_funds",   # эффективная ставка ФРС, % годовых
    "DGS10": "us_10y",         # доходность 10-летних UST, % годовых
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def _get_json(url: str, params: dict) -> dict:
    resp = httpx.get(url, params=params, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def _latest_observation(series_id: str, api_key: str) -> tuple[str, float] | None:
    """Последнее доступное (непустое) наблюдение серии: (дата ISO, значение)."""
    data = _get_json(OBSERVATIONS_URL, {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 10,  # с запасом: свежие наблюдения могут быть пустыми (".")
    })
    for obs in data.get("observations", []):
        raw = obs.get("value")
        if raw in (None, "", "."):
            continue
        try:
            return obs["date"], float(raw)
        except (KeyError, ValueError):
            continue
    return None


@register
class FredConnector(BaseConnector):
    name = "fred"
    kind = SourceKind.MACRO

    def fetch(self) -> Iterable[RawItem]:
        api_key = get_settings().fred_api_key
        if not api_key:
            log.warning("fred_no_api_key", hint="задайте GEO_FRED_API_KEY")
            return
        for series_id, indicator in SERIES.items():
            try:
                latest = _latest_observation(series_id, api_key)
            except Exception as exc:  # noqa: BLE001 — один ряд не валит остальные
                log.warning("fred_series_failed", series=series_id, error=str(exc))
                continue
            if latest is None:
                log.warning("fred_no_data", series=series_id)
                continue
            iso_date, value = latest
            # processing._process_macro ожидает дату в формате dd.mm.yyyy (как ЦБ).
            date = datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d.%m.%Y")
            log.info("fred_value", indicator=indicator, value=value, date=date)
            yield RawItem(
                source=self.name,
                external_id=f"{indicator}:{date}",
                raw_text=f"FRED {indicator}={value} on {date}",
                payload={"kind": "macro", "indicator": indicator, "value": value, "date": date},
            )
