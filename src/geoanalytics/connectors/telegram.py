"""H3 (Волна 4): Telegram-каналы через веб-превью t.me/s/<канал>.

Публичные каналы отдают последние ~20 постов как HTML без авторизации и
API-ключей — этого достаточно для регулярного опроса (новости ~15м, дедуп по
(source, external_id) отсеет уже виденные). История глубже превью и закрытые
каналы потребуют MTProto (Telethon) — сознательно отложено.

Парсинг — на регулярках по стабильным маркерам виджета (data-post,
tgme_widget_message_text, time datetime=...): bs4 в зависимостях нет, и для
v1 этого хватает; тесты на сохранённом фикстур-HTML поймают смену вёрстки.

Первый канал — ifax_go (Интерфакс, рынки): новостной стиль, домен-шифт
дистиллятов минимален. Аналитические каналы добавятся списком пользователя
(вместе с F4 rumor/fact и F7 reliability).
"""

from __future__ import annotations

import html as html_mod
import re
from collections.abc import Iterable
from datetime import datetime
from email.utils import format_datetime

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import get_settings
from geoanalytics.connectors.base import BaseConnector, RawItem
from geoanalytics.connectors.registry import register
from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import SourceKind

_TIMEOUT = 20.0
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; geoanalytics/1.0)"}

# Пост целиком: от data-post до следующего data-post (или конца документа).
_POST_RE = re.compile(r'data-post="([^"]+)"(.*?)(?=data-post="|\Z)', re.DOTALL)
_TEXT_RE = re.compile(
    r'tgme_widget_message_text[^>]*>(.*?)</div>', re.DOTALL)
_TIME_RE = re.compile(r'<time[^>]*datetime="([^"]+)"')
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(fragment: str) -> str:
    """HTML-фрагмент поста → плоский текст (переносы строк из <br>)."""
    text = _BR_RE.sub("\n", fragment)
    text = _TAG_RE.sub("", text)
    return html_mod.unescape(text).strip()


def parse_channel_html(html: str) -> list[dict]:
    """Чистый парсер превью канала: список постов {post_id, text, published}.

    `published` — RFC 822 (формат RSS): дальше дату разбирает общий
    `parse_rss_date` в processing. Служебные посты без текста пропускаются."""
    posts: list[dict] = []
    for m in _POST_RE.finditer(html):
        post_id, block = m.group(1), m.group(2)
        text_m = _TEXT_RE.search(block)
        if not text_m:
            continue
        text = _strip_html(text_m.group(1))
        if not text:
            continue
        published = None
        time_m = _TIME_RE.search(block)
        if time_m:
            try:
                published = format_datetime(
                    datetime.fromisoformat(time_m.group(1)))
            except ValueError:
                published = None
        posts.append({"post_id": post_id, "text": text, "published": published})
    return posts


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def _fetch_preview(channel: str) -> str:
    # t.me блокируется в РФ-сети без общесистемного VPN (Pi, Фаза 2): тянем превью через тот
    # же SOCKS-прокси, что и алерты (settings.telegram_proxy / Xray). Пусто — прямое соединение.
    proxy = get_settings().telegram_proxy
    with httpx.Client(proxy=proxy, timeout=_TIMEOUT, headers=_HEADERS,
                      follow_redirects=True) as client:
        resp = client.get(f"https://t.me/s/{channel}")
    resp.raise_for_status()
    return resp.text


@register
class TelegramConnector(BaseConnector):
    """Посты публичных Telegram-каналов (веб-превью, без API)."""

    name = "telegram"
    kind: SourceKind = SourceKind.NEWS

    def __init__(self) -> None:
        self._log = get_logger("connector.telegram")

    def fetch(self) -> Iterable[RawItem]:
        channels = [c.strip() for c in
                    get_settings().telegram_channels.split(",") if c.strip()]
        for channel in channels:
            self._log.info("fetching_channel", channel=channel)
            try:
                page = _fetch_preview(channel)
            except Exception as exc:
                # Сбой одного канала не роняет остальные/цикл (как в RSS).
                self._log.warning("channel_fetch_error", channel=channel,
                                  error=str(exc))
                continue
            for post in parse_channel_html(page):
                lines = post["text"].split("\n", 1)
                title = lines[0][:500]
                summary = lines[1].strip() if len(lines) > 1 else ""
                yield RawItem(
                    source=self.name,
                    external_id=post["post_id"],
                    raw_text=post["text"],
                    payload={
                        "title": title,
                        "summary": summary,
                        "url": f"https://t.me/{post['post_id']}",
                        "published": post["published"],
                        "channel": channel,
                    },
                )
