"""Тесты H3: коннектор Telegram-каналов (парсер веб-превью)."""

from __future__ import annotations

from unittest.mock import patch

from geoanalytics.connectors.telegram import (
    TelegramConnector,
    _strip_html,
    parse_channel_html,
)

_HTML = """
<div class="tgme_widget_message" data-post="ifax_go/101">
  <div class="tgme_widget_message_text js-message_text" dir="auto">
    <b>ЦБ снизил ставку до 14%</b><br/>Решение совпало с консенсусом &amp; ожиданиями.
    <a href="https://example.com">Подробнее</a>
  </div>
  <time datetime="2026-06-12T09:30:00+00:00">9:30</time>
</div>
<div class="tgme_widget_message" data-post="ifax_go/102">
  <a class="tgme_widget_message_photo_wrap" href="x"></a>
  <time datetime="2026-06-12T10:00:00+00:00">10:00</time>
</div>
<div class="tgme_widget_message" data-post="ifax_go/103">
  <div class="tgme_widget_message_text js-message_text" dir="auto">Сбер отчитался за квартал</div>
</div>
"""


def test_parse_posts_text_and_date():
    """Текст без тегов, перенос из <br>, дата в RFC 822."""
    posts = parse_channel_html(_HTML)
    ids = [p["post_id"] for p in posts]
    assert ids == ["ifax_go/101", "ifax_go/103"]  # 102 — фото без текста, пропущен
    first = posts[0]
    assert first["text"].startswith("ЦБ снизил ставку до 14%\n")
    assert "&amp;" not in first["text"] and "&" in first["text"]
    assert "<" not in first["text"]
    assert first["published"] == "Fri, 12 Jun 2026 09:30:00 +0000"
    assert posts[1]["published"] is None  # нет <time> — без даты, не падаем


def test_strip_html_entities_and_tags():
    assert _strip_html("a<br/>b &quot;c&quot; <i>d</i>") == 'a\nb "c" d'


def test_connector_yields_rawitems():
    """fetch: title = первая строка, external_id = канал/пост, url собран."""
    with (
        patch("geoanalytics.connectors.telegram._fetch_preview",
              return_value=_HTML),
        patch("geoanalytics.connectors.telegram.get_settings") as gs,
    ):
        gs.return_value.telegram_channels = "ifax_go"
        items = list(TelegramConnector().fetch())
    assert len(items) == 2
    it = items[0]
    assert it.source == "telegram"
    assert it.external_id == "ifax_go/101"
    assert it.payload["title"] == "ЦБ снизил ставку до 14%"
    assert it.payload["url"] == "https://t.me/ifax_go/101"
    assert it.payload["channel"] == "ifax_go"
    assert items[1].payload["summary"] == ""


def test_connector_channel_error_does_not_raise():
    """Сбой канала логируется и пропускается (как у RSS-лент)."""
    with (
        patch("geoanalytics.connectors.telegram._fetch_preview",
              side_effect=RuntimeError("boom")),
        patch("geoanalytics.connectors.telegram.get_settings") as gs,
    ):
        gs.return_value.telegram_channels = "ifax_go"
        assert list(TelegramConnector().fetch()) == []
