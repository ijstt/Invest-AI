"""Коннектор котировок МосБиржи через ISS API (публичный, без ключа).

Берём срез по основному режиму торгов акциями (TQBR): по каждому инструменту —
последняя цена, изменение и объём. Исторические свечи подтянем в analytics (M2).

Документация ISS: https://iss.moex.com/iss/reference/
"""

from __future__ import annotations

from collections.abc import Iterable

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from geoanalytics.connectors.base import BaseConnector, RawItem
from geoanalytics.connectors.registry import register
from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import SourceKind
from geoanalytics.storage.seed import ISSUERS

log = get_logger("connector.moex")

ISS_URL = (
    "https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json"
)
# Индексы (IMOEX) живут на отдельном рынке/борде; без регулярного среза индекс
# обновлялся только бэкфиллом и отставал на день — а это бенчмарк (B4) и
# рыночный фактор атрибуции (G3), его свежесть критична.
INDEX_URL = (
    "https://iss.moex.com/iss/engines/stock/markets/index/boards/SNDX/securities.json"
)
INDEX_TICKERS = ("IMOEX",)
# Тянем срез только по отслеживаемым тикерам, а не весь TQBR: полный листинг — сотни
# бумаг (десятки/сотни КБ), и при медленном/проксированном канале чтение тела отваливается
# по таймауту. Узкий запрос (securities=<список> + iss.only + нужные колонки) — единицы КБ.
_SEC_COLS = "SECID,SHORTNAME,SECNAME,ISIN"
_MD_COLS = "SECID,LAST,OPEN,HIGH,LOW,LASTTOPREVPRICE,VALTODAY,SYSTIME"
_IDX_MD_COLS = "SECID,CURRENTVALUE,OPENVALUE,HIGH,LOW,LASTCHANGEPRC,VALTODAY,SYSTIME"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def _get_json(url: str, params: dict) -> dict:
    """GET с ретраями и таймаутом."""
    resp = httpx.get(url, params=params, timeout=60.0)
    resp.raise_for_status()
    return resp.json()


def _rows(block: dict) -> list[dict]:
    """Превращает блок ISS {columns, data} в список словарей."""
    columns = block["columns"]
    return [dict(zip(columns, row, strict=False)) for row in block["data"]]


@register
class MoexConnector(BaseConnector):
    name = "moex"
    kind = SourceKind.MARKET

    def fetch(self) -> Iterable[RawItem]:
        # securities — справочная инфа, marketdata — текущие котировки.
        params = {
            "iss.meta": "off",
            "iss.only": "securities,marketdata",
            "securities": ",".join(ISSUERS),
            "securities.columns": _SEC_COLS,
            "marketdata.columns": _MD_COLS,
        }
        data = _get_json(ISS_URL, params)
        securities = {r["SECID"]: r for r in _rows(data["securities"])}
        marketdata = _rows(data["marketdata"])
        log.info("moex_snapshot", instruments=len(marketdata))

        for md in marketdata:
            secid = md.get("SECID")
            if not secid:
                continue
            sec = securities.get(secid, {})
            payload = {
                "ticker": secid,
                "name": sec.get("SHORTNAME") or sec.get("SECNAME"),
                "isin": sec.get("ISIN"),
                "board": "TQBR",
                "last": md.get("LAST"),
                "open": md.get("OPEN"),
                "high": md.get("HIGH"),
                "low": md.get("LOW"),
                "change_pct": md.get("LASTTOPREVPRICE"),
                "volume": md.get("VALTODAY"),
                "updated": md.get("SYSTIME"),
            }
            # raw_text для дедупа — компактная сводка котировки.
            text = f"{secid} last={payload['last']} chg={payload['change_pct']} @ {payload['updated']}"
            yield RawItem(
                source=self.name,
                external_id=f"{secid}:{payload['updated']}",
                raw_text=text,
                payload=payload,
            )

        yield from self._fetch_indices()

    def _fetch_indices(self) -> Iterable[RawItem]:
        """Срез индексов (SNDX): payload в формате _process_market."""
        try:
            data = _get_json(INDEX_URL, {
                "iss.meta": "off",
                "iss.only": "marketdata",
                "securities": ",".join(INDEX_TICKERS),
                "marketdata.columns": _IDX_MD_COLS,
            })
            marketdata = _rows(data["marketdata"])
        except Exception as exc:  # noqa: BLE001 — индекс не валит срез акций
            log.warning("moex_index_failed", error=str(exc))
            return
        for md in marketdata:
            secid = md.get("SECID")
            if not secid:
                continue
            payload = {
                "ticker": secid,
                "name": secid,
                "board": "SNDX",
                "last": md.get("CURRENTVALUE"),
                "open": md.get("OPENVALUE"),
                "high": md.get("HIGH"),
                "low": md.get("LOW"),
                "change_pct": md.get("LASTCHANGEPRC"),
                "volume": md.get("VALTODAY"),
                "updated": md.get("SYSTIME"),
            }
            text = (f"{secid} last={payload['last']} chg={payload['change_pct']} "
                    f"@ {payload['updated']}")
            yield RawItem(
                source=self.name,
                external_id=f"{secid}:{payload['updated']}",
                raw_text=text,
                payload=payload,
            )
