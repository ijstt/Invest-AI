"""Сырьевые макро-факторы через фьючерсы MOEX (FORTS): нефть Brent.

Берём ближний фьючерс с доступной последней ценой (фронт-месяц = ближайшая дата
экспирации) и пишем как макро-индикатор ('brent'). Это сырьевой фактор для
корреляций, атрибуции и what-if.

Код серии FORTS: Brent — BR. Драгметаллы (gold/silver/platinum/palladium) с
2026-06-13 идут из учётных цен ЦБ (коннектор cbr) — у ЦБ есть многолетняя
история без склейки фьючерсных контрактов, у FORTS — нет.
Источник может быть недоступен из песочницы (сетевые ограничения).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from geoanalytics.connectors.base import BaseConnector, RawItem
from geoanalytics.connectors.registry import register
from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import SourceKind

log = get_logger("connector.commodities")

FORTS_URL = "https://iss.moex.com/iss/engines/futures/markets/forts/securities.json"
# Коды месяцев фьючерсов (1..12 → F..Z): месячный контракт = PREFIX + буква месяца + цифра года.
_MONTH_CODES = " FGHJKMNQUVXZ"
# Сколько ближайших месячных контрактов перечислять как кандидатов фронт-месяца.
_HORIZON_MONTHS = 8


def _candidate_contracts(prefix: str, today: date) -> list[str]:
    """SECID'ы месячных контрактов серии на ближайшие _HORIZON_MONTHS месяцев."""
    out, y, m = [], today.year, today.month
    for _ in range(_HORIZON_MONTHS):
        out.append(f"{prefix}{_MONTH_CODES[m]}{y % 10}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def _get_json(url: str, params: dict) -> dict:
    resp = httpx.get(url, params=params, timeout=60.0)
    resp.raise_for_status()
    return resp.json()


def _rows(block: dict) -> list[dict]:
    cols = block["columns"]
    return [dict(zip(cols, row, strict=False)) for row in block["data"]]


def _front_month_price(prefix: str, today_d: date) -> tuple[str, float] | None:
    """Цена и SECID фронт-месячного контракта серии `prefix`. None, если данных нет."""
    # Узкий запрос по конкретным контрактам: тянуть весь листинг FORTS (тысячи бумаг)
    # дорого и на медленном/проксированном канале отваливается по таймауту чтения.
    params = {
        "iss.meta": "off",
        "iss.only": "securities,marketdata",
        "securities": ",".join(_candidate_contracts(prefix, today_d)),
        "securities.columns": "SECID,LASTDELDATE",
        "marketdata.columns": "SECID,LAST",
    }
    data = _get_json(FORTS_URL, params)
    md = {r["SECID"]: r for r in _rows(data["marketdata"])}
    candidates = []
    for r in _rows(data["securities"]):
        secid = r.get("SECID", "")
        last = (md.get(secid) or {}).get("LAST")
        deldate = r.get("LASTDELDATE")
        if last and deldate:
            candidates.append((deldate, secid, float(last)))
    if not candidates:
        return None
    # Фронт-месяц = ближайшая дата экспирации (надёжнее лексикографики на границе года).
    _deldate, secid, price = sorted(candidates)[0]
    return secid, price


class _FuturesMacroConnector(BaseConnector):
    """Базовый коннектор сырьевого макро-индикатора через фронт-месячный фьючерс FORTS."""

    kind = SourceKind.MACRO
    prefix: str = ""        # код серии FORTS (BR/GD/SILV)
    indicator: str = ""     # имя макро-индикатора в БД

    def fetch(self) -> Iterable[RawItem]:
        today_d = datetime.now(UTC).date()
        found = _front_month_price(self.prefix, today_d)
        if found is None:
            log.warning("commodity_no_contracts", indicator=self.indicator)
            return
        secid, price = found
        today = today_d.strftime("%d.%m.%Y")
        log.info("commodity_price", indicator=self.indicator, contract=secid, price=price)
        yield RawItem(
            source=self.name,
            external_id=f"{self.indicator}:{today}",
            raw_text=f"{self.indicator} {price} ({secid}) on {today}",
            payload={"kind": "macro", "indicator": self.indicator, "value": price, "date": today},
        )


@register
class BrentConnector(_FuturesMacroConnector):
    name = "brent"
    prefix = "BR"
    indicator = "brent"
