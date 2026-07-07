"""Базовый RSS-коннектор новостей.

Большинство новостных источников (Интерфакс, РБК, Ведомости, Коммерсантъ)
отдают одинаковые RSS/Atom-ленты. Общая логика разбора вынесена сюда: конкретному
источнику достаточно объявить `name` и список `FEEDS`. Полный текст статьи можно
дотягивать позже на этапе обогащения.
"""

from __future__ import annotations

from collections.abc import Iterable

import feedparser
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from geoanalytics.connectors.base import BaseConnector, RawItem
from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import SourceKind

# Таймаут на загрузку ленты. ВАЖНО: сами качаем через httpx, а не отдаём URL в
# feedparser.parse(url) — тот фетчит через urllib БЕЗ таймаута и при сетевом стопоре
# вешает весь цикл планировщика навсегда (наблюдалось на ленте РБК).
_FEED_TIMEOUT = 20.0
# Часть лент отклоняет дефолтный User-Agent — представляемся обычным клиентом.
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; geoanalytics/1.0)"}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def _fetch_feed(url: str) -> bytes:
    """Скачивает RSS/Atom-ленту с таймаутом и ретраями. Возвращает сырые байты."""
    resp = httpx.get(url, timeout=_FEED_TIMEOUT, headers=_HEADERS, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


class RssConnector(BaseConnector):
    """Универсальный коннектор RSS-лент.

    Подклассы задают `name` и `FEEDS` (список URL). При необходимости можно
    переопределить `kind`, но для новостных лент по умолчанию — NEWS.
    """

    kind: SourceKind = SourceKind.NEWS
    FEEDS: list[str] = []

    def __init__(self) -> None:
        # Отдельный логгер на каждый источник для прозрачности в логах.
        self._log = get_logger(f"connector.{self.name}")

    def fetch(self) -> Iterable[RawItem]:
        for url in self.FEEDS:
            self._log.info("fetching_feed", url=url)
            try:
                content = _fetch_feed(url)
            except Exception as exc:
                # Сбой одной ленты не должен ронять остальные источники/цикл.
                self._log.warning("feed_fetch_error", url=url, error=str(exc))
                continue
            feed = feedparser.parse(content)
            if feed.bozo:
                self._log.warning("feed_parse_error", url=url, error=str(feed.bozo_exception))
            for entry in feed.entries:
                title = entry.get("title", "").strip()
                summary = entry.get("summary", "").strip()
                if not title:
                    continue
                # Текст для дедупа/обработки: заголовок + краткое описание.
                text = f"{title}\n\n{summary}".strip()
                yield RawItem(
                    source=self.name,
                    external_id=entry.get("id") or entry.get("link"),
                    raw_text=text,
                    payload={
                        "title": title,
                        "summary": summary,
                        "url": entry.get("link"),
                        "published": entry.get("published"),
                    },
                )
