"""Коннектор новостей «Ведомости» через RSS-ленты.

Берём общую ленту новостей и рубрику «Экономика». Разбор — в общей базе
RssConnector.
"""

from __future__ import annotations

from geoanalytics.connectors.registry import register
from geoanalytics.connectors.rss import RssConnector


@register
class VedomostiConnector(RssConnector):
    name = "vedomosti"
    FEEDS = [
        "https://www.vedomosti.ru/rss/news",
        "https://www.vedomosti.ru/rss/rubric/economics",
    ]
