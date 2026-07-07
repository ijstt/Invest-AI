"""Коннектор новостей РБК через RSS-ленты.

РБК в 2026 свернул посекционные `news/{id}/full.rss` — живой остался только общий
полнотекстовый поток `news/30/full.rss` (он же включает экономику). Прежняя лента
«Экономика» (`news/20`) отдаёт 404; убрана, чтобы не копить тихие feed_fetch_error.
Разбор ленты — в общей базе RssConnector.
"""

from __future__ import annotations

from geoanalytics.connectors.registry import register
from geoanalytics.connectors.rss import RssConnector


@register
class RbcConnector(RssConnector):
    name = "rbc"
    # Единственная живая полнотекстовая лента РБК — общий поток новостей (включает экономику).
    FEEDS = [
        "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",
    ]
