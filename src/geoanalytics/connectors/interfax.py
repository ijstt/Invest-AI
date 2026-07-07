"""Коннектор новостей Интерфакс через RSS-ленты.

Интерфакс в 2026 перевёл RSS на путь без `.asp`: канонический поток —
`https://www.interfax.ru/rss` (общий, включает бизнес). Прежние `rss.asp`
(301→/rss) и `business/rss.asp` (404) убраны: первый держался лишь на редиректе,
второй мёртв. Разбор ленты — в общей базе RssConnector.
"""

from __future__ import annotations

from geoanalytics.connectors.registry import register
from geoanalytics.connectors.rss import RssConnector


@register
class InterfaxConnector(RssConnector):
    name = "interfax"
    # Канонический общий поток Интерфакса (включает бизнес-новости).
    FEEDS = [
        "https://www.interfax.ru/rss",
    ]
