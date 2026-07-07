"""Коннектор новостей «Коммерсантъ» через RSS-ленты.

Берём общую ленту новостей и раздел «Экономика». Разбор — в общей базе
RssConnector.
"""

from __future__ import annotations

from geoanalytics.connectors.registry import register
from geoanalytics.connectors.rss import RssConnector


@register
class KommersantConnector(RssConnector):
    name = "kommersant"
    FEEDS = [
        "https://www.kommersant.ru/RSS/news.xml",
        "https://www.kommersant.ru/RSS/section-economics.xml",
    ]
