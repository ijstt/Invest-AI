"""Тесты MTProto-коннектора (чистые помощники; telethon не требуется)."""

from __future__ import annotations

from datetime import UTC, datetime

from geoanalytics.connectors.telegram_mtproto import (
    build_raw_item,
    parse_backfill_window,
    parse_channel_ref,
    parse_private_channels,
    post_identity,
    split_title_summary,
)


class TestParseChannelRef:
    def test_invite_links(self):
        assert parse_channel_ref("https://t.me/+7lXwq8XTVWJjMzgy") == (
            "invite", "7lXwq8XTVWJjMzgy")
        assert parse_channel_ref("t.me/joinchat/AAAAA_bbb") == ("invite", "AAAAA_bbb")

    def test_usernames(self):
        assert parse_channel_ref("@prostoecon") == ("username", "prostoecon")
        assert parse_channel_ref("https://t.me/centralbank_russia") == (
            "username", "centralbank_russia")
        assert parse_channel_ref("ifax_go") == ("username", "ifax_go")

    def test_invite_not_mistaken_for_username(self):
        kind, _ = parse_channel_ref("https://t.me/+7lXwq8XTVWJjMzgy")
        assert kind == "invite"

    def test_empty_and_garbage(self):
        assert parse_channel_ref("") is None
        assert parse_channel_ref("   ") is None

    def test_trailing_slash(self):
        assert parse_channel_ref("https://t.me/prostoecon/") == (
            "username", "prostoecon")

    def test_numeric_id_forms(self):
        # Приватные каналы без username адресуются числовым ID.
        assert parse_channel_ref("id1616051377") == ("id", "1616051377")
        assert parse_channel_ref("1616051377") == ("id", "1616051377")
        assert parse_channel_ref("https://t.me/c/1616051377/55") == ("id", "1616051377")
        assert parse_channel_ref("t.me/c/1616051377") == ("id", "1616051377")

    def test_id_prefix_not_mistaken_for_username(self):
        kind, _ = parse_channel_ref("id1616051377")
        assert kind == "id"


class TestParsePrivateChannels:
    def test_mixed_list_skips_garbage(self):
        raw = "https://t.me/+HASH123, @prostoecon, , bad ref!!"
        assert parse_private_channels(raw) == [
            ("invite", "HASH123"), ("username", "prostoecon")]

    def test_empty(self):
        assert parse_private_channels("") == []


class TestSplitTitleSummary:
    def test_first_line_is_title(self):
        title, summary = split_title_summary("Заголовок\nстрока 2\nстрока 3")
        assert title == "Заголовок"
        assert summary == "строка 2\nстрока 3"

    def test_single_line(self):
        title, summary = split_title_summary("Только заголовок")
        assert title == "Только заголовок"
        assert summary == ""


class TestPostIdentity:
    def test_public_channel_url(self):
        ext, url = post_identity("prostoecon", "prostoecon", 12345, 678)
        assert ext == "prostoecon/678"
        assert url == "https://t.me/prostoecon/678"

    def test_private_channel_url(self):
        ext, url = post_identity("id12345", None, 12345, 678)
        assert ext == "id12345/678"
        assert url == "https://t.me/c/12345/678"


class TestBuildRawItem:
    def test_public_post_fields(self):
        date = datetime(2025, 12, 1, 9, 30, 0, tzinfo=UTC)
        item = build_raw_item("prostoecon", "prostoecon", 111, 678,
                              "Заголовок\nтело поста", date)
        assert item.source == "telegram_mtproto"
        assert item.external_id == "prostoecon/678"
        assert item.raw_text == "Заголовок\nтело поста"
        assert item.payload["title"] == "Заголовок"
        assert item.payload["summary"] == "тело поста"
        assert item.payload["url"] == "https://t.me/prostoecon/678"
        assert item.payload["channel"] == "prostoecon"
        # RFC 822 — парсится core.dates.parse_rss_date.
        assert item.payload["published"] == "Mon, 01 Dec 2025 09:30:00 +0000"

    def test_private_post_no_username(self):
        item = build_raw_item("id111", None, 111, 5, "Текст", None)
        assert item.payload["url"] == "https://t.me/c/111/5"
        assert item.payload["published"] is None


class TestParseBackfillWindow:
    def test_explicit_window_utc(self):
        since, until = parse_backfill_window("2025-12-01", "2026-01-01")
        assert since == datetime(2025, 12, 1, tzinfo=UTC)
        assert until == datetime(2026, 1, 1, tzinfo=UTC)
        assert since < until

    def test_until_defaults_to_now(self):
        since, until = parse_backfill_window("2025-12-01")
        assert since == datetime(2025, 12, 1, tzinfo=UTC)
        assert until.tzinfo is UTC
        assert until > since
