"""Коннектор ЦБ РФ: официальные курсы валют, ключевая ставка и драгметаллы.

- Курсы валют: дневной XML https://www.cbr.ru/scripts/XML_daily.asp
- Ключевая ставка: текущее значение со страницы ключевой ставки.
- Учётные цены металлов (золото/серебро/платина/палладий, ₽/грамм):
  https://www.cbr.ru/scripts/xml_metall.asp — окно последних дней, дедуп raw-слоя
  по external_id делает повторный опрос идемпотентным.

Курсы дают `fx_rates`; ставка и металлы идут в `macro_series`.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import httpx
from selectolax.parser import HTMLParser
from tenacity import retry, stop_after_attempt, wait_exponential

from geoanalytics.connectors.base import BaseConnector, RawItem
from geoanalytics.connectors.registry import register
from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import SourceKind

log = get_logger("connector.cbr")

FX_DAILY_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
KEY_RATE_URL = "https://www.cbr.ru/hd_base/KeyRate/"

# Какие валюты нас интересуют (остальные пропускаем).
CURRENCIES = {"USD", "EUR", "CNY"}

# Окно живого опроса металлов: ЦБ публикует цены на следующий рабочий день,
# с запасом на праздники берём последнюю неделю.
_METALS_WINDOW_DAYS = 7


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def _get(url: str, params: dict | None = None) -> httpx.Response:
    resp = httpx.get(url, params=params, timeout=30.0)
    resp.raise_for_status()
    return resp


@register
class CbrConnector(BaseConnector):
    name = "cbr"
    kind = SourceKind.MACRO

    def fetch(self) -> Iterable[RawItem]:
        yield from self._fetch_fx()
        item = self._fetch_key_rate()
        if item is not None:
            yield item
        yield from self._fetch_metals()

    def _fetch_fx(self) -> Iterable[RawItem]:
        """Дневные курсы валют (XML)."""
        resp = _get(FX_DAILY_URL)
        # XML_daily отдаётся в windows-1251; декодируем корректно.
        tree = HTMLParser(resp.content.decode("windows-1251"))
        date_attr = tree.css_first("valcurs")
        on_date = date_attr.attributes.get("date") if date_attr else None
        count = 0
        for node in tree.css("valute"):
            code_node = node.css_first("charcode")
            if code_node is None:
                continue
            code = code_node.text(strip=True)
            if code not in CURRENCIES:
                continue
            value_raw = node.css_first("value").text(strip=True).replace(",", ".")
            nominal = node.css_first("nominal").text(strip=True).replace(",", ".")
            rate = float(value_raw) / float(nominal)
            count += 1
            yield RawItem(
                source=self.name,
                external_id=f"fx:{code}:{on_date}",
                raw_text=f"CBR FX {code}={rate:.4f} on {on_date}",
                payload={"kind": "fx", "currency": code, "value": rate, "date": on_date},
            )
        log.info("cbr_fx", currencies=count, date=on_date)

    def _fetch_key_rate(self) -> RawItem | None:
        """Текущая ключевая ставка (парсинг страницы ЦБ)."""
        try:
            resp = _get(KEY_RATE_URL)
        except Exception as exc:  # noqa: BLE001 — источник может быть недоступен
            log.warning("cbr_key_rate_failed", error=str(exc))
            return None
        tree = HTMLParser(resp.text)
        # Первая строка таблицы — заголовок (th: Дата, Ставка), данные идут ниже.
        # Берём первую строку с ≥2 ячейками td: верхняя из них — самая свежая ставка.
        cells = []
        for tr in tree.css("table.data tbody tr"):
            tds = tr.css("td")
            if len(tds) >= 2:
                cells = tds
                break
        if len(cells) < 2:
            log.warning("cbr_key_rate_no_data")
            return None
        date = cells[0].text(strip=True)
        rate = float(cells[1].text(strip=True).replace(",", "."))
        log.info("cbr_key_rate", rate=rate, date=date)
        return RawItem(
            source=self.name,
            external_id=f"key_rate:{date}",
            raw_text=f"CBR key_rate={rate} on {date}",
            payload={"kind": "macro", "indicator": "key_rate", "value": rate, "date": date},
        )

    def _fetch_metals(self) -> Iterable[RawItem]:
        """Учётные цены металлов за последние дни (xml_metall, ₽/грамм)."""
        # Парсер живёт в analytics.history (общий с бэкфиллом) — один формат XML.
        from geoanalytics.analytics.history import (
            METALL_DYNAMIC_URL,
            parse_metal_dynamic,
        )

        today = datetime.now(UTC).date()
        try:
            resp = _get(METALL_DYNAMIC_URL, params={
                "date_req1": (today - timedelta(days=_METALS_WINDOW_DAYS))
                .strftime("%d/%m/%Y"),
                "date_req2": today.strftime("%d/%m/%Y"),
            })
        except Exception as exc:  # noqa: BLE001 — металлы не валят валюты/ставку
            log.warning("cbr_metals_failed", error=str(exc))
            return
        points = parse_metal_dynamic(resp.content)
        for indicator, ts, value in points:
            on_date = ts.strftime("%d.%m.%Y")
            yield RawItem(
                source=self.name,
                external_id=f"{indicator}:{on_date}",
                raw_text=f"CBR {indicator}={value} RUB/g on {on_date}",
                payload={"kind": "macro", "indicator": indicator,
                         "value": value, "date": on_date, "unit": "RUB/g"},
            )
        log.info("cbr_metals", points=len(points))
