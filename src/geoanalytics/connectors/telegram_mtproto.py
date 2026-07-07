"""H3 (Волна 4): ЗАКРЫТЫЕ Telegram-каналы через MTProto (Telethon).

Веб-превью t.me/s/<канал> (connectors/telegram.py) видит только публичные каналы и
~20 последних постов. Закрытые каналы и глубокая история требуют клиентского MTProto:
авторизуемся аккаунт-сессией пользователя (он уже подписчик), резолвим инвайт-ссылку
`t.me/+HASH` и читаем последние сообщения.

Зависимость telethon — ОПЦИОНАЛЬНАЯ (группа `mtproto`), импортируется ЛЕНИВО внутри
fetch: без неё/без кредов/без файла-сессии источник тихо пропускается (graceful
degradation, как FRED без ключа). Разовый интерактивный логин (телефон + код) —
scripts/telegram_login.py создаёт файл-сессию; дальше работа неинтерактивна.

Чистые помощники (`parse_channel_ref`, `split_title_summary`, `post_identity`) —
основной предмет тестов; сеть/Telethon в тестах не нужны.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime

from config.settings import get_settings
from geoanalytics.connectors.base import BaseConnector, RawItem
from geoanalytics.connectors.registry import register
from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import SourceKind

log = get_logger("connector.telegram_mtproto")

# Формат даты поста — RFC 822 (как RSS), чтобы конвейер распарсил `published`
# через core.dates.parse_rss_date и сохранил исторический published_at.
_PUBLISHED_FMT = "%a, %d %b %Y %H:%M:%S %z"

# Инвайт-ссылка приватного канала: t.me/+HASH или t.me/joinchat/HASH.
_INVITE_RE = re.compile(r"(?:t\.me/|^)(?:\+|joinchat/)([A-Za-z0-9_-]+)$")
# Числовой ID приватного канала (без username): t.me/c/<id>[/<msg>] или id<id>.
_C_ID_RE = re.compile(r"(?:t\.me/c/|id)(\d{5,})(?:/\d+)?$")
# Чистые цифры — тоже трактуем как ID канала.
_BARE_ID_RE = re.compile(r"^\d{5,}$")
# Публичный @username (с/без @, с/без префикса t.me/).
_USERNAME_RE = re.compile(r"(?:t\.me/|@|^)([A-Za-z][A-Za-z0-9_]{3,})$")


def parse_channel_ref(raw: str) -> tuple[str, str] | None:
    """Ссылку/идентификатор канала → ('invite', hash) | ('username', name) | None.

    Инвайт-ссылка (приватный канал) проверяется первой: её хвост не является
    валидным username. Чистая функция.
    """
    ref = raw.strip().rstrip("/")
    if not ref:
        return None
    m = _INVITE_RE.search(ref)
    if m:
        return ("invite", m.group(1))
    # ID проверяем ДО username: 'id123456' иначе матчнулось бы как username.
    m = _C_ID_RE.search(ref)
    if m:
        return ("id", m.group(1))
    if _BARE_ID_RE.match(ref):
        return ("id", ref)
    m = _USERNAME_RE.search(ref)
    if m:
        return ("username", m.group(1))
    return None


def parse_private_channels(raw: str) -> list[tuple[str, str]]:
    """Список из settings.telegram_private_channels → разобранные ссылки (битые пропускаем)."""
    out: list[tuple[str, str]] = []
    for part in raw.split(","):
        parsed = parse_channel_ref(part)
        if parsed is not None:
            out.append(parsed)
    return out


def split_title_summary(text: str) -> tuple[str, str]:
    """Текст поста → (заголовок ≤500, остаток). Первая строка — заголовок."""
    lines = text.split("\n", 1)
    title = lines[0][:500]
    summary = lines[1].strip() if len(lines) > 1 else ""
    return title, summary


def post_identity(channel_key: str, username: str | None, chan_id: int,
                  msg_id: int) -> tuple[str, str]:
    """(external_id, url) поста. Публичный канал — t.me/<username>/<id>,
    приватный — t.me/c/<chan_id>/<id> (стабильно и уникально)."""
    external_id = f"{channel_key}/{msg_id}"
    if username:
        url = f"https://t.me/{username}/{msg_id}"
    else:
        url = f"https://t.me/c/{chan_id}/{msg_id}"
    return external_id, url


def build_raw_item(channel_key: str, username: str | None, chan_id: int,
                   msg_id: int, text: str, date: datetime | None) -> RawItem:
    """Сообщение Telegram → RawItem (единый вид для свежего и историч. сбора). Чистая."""
    external_id, url = post_identity(channel_key, username, chan_id, msg_id)
    title, summary = split_title_summary(text)
    published = date.strftime(_PUBLISHED_FMT) if isinstance(date, datetime) else None
    return RawItem(
        source="telegram_mtproto",
        external_id=external_id,
        raw_text=text,
        payload={
            "title": title, "summary": summary, "url": url,
            "published": published, "channel": channel_key,
        },
    )


def parse_backfill_window(since: str, until: str | None = None) -> tuple[datetime, datetime]:
    """'YYYY-MM-DD' → (since_utc, until_utc). until по умолчанию — текущий момент.

    Границы tz-aware UTC. until трактуется как начало указанного дня (верхняя граница
    исключающая); since — начало дня (включающая). Чистая (кроме datetime.now при until=None)."""
    since_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=UTC)
    until_dt = (datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=UTC)
                if until else datetime.now(tz=UTC))
    return since_dt, until_dt


async def _collect(api_id: int, api_hash: str, session_path: str,
                   refs: list[tuple[str, str]], limit: int) -> list[RawItem]:
    """Логинимся сохранённой сессией и собираем последние сообщения каналов."""
    from telethon import TelegramClient
    from telethon.tl.functions.messages import (
        CheckChatInviteRequest,
        ImportChatInviteRequest,
    )
    from telethon.tl.types import ChatInviteAlready

    items: list[RawItem] = []
    client = TelegramClient(session_path, api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            log.warning("mtproto_not_authorized",
                        hint="запустите scripts/telegram_login.py")
            return items
        for kind, value in refs:
            try:
                entity = await _resolve(client, kind, value,
                                        CheckChatInviteRequest,
                                        ImportChatInviteRequest, ChatInviteAlready)
                if entity is None:
                    continue
                username = getattr(entity, "username", None)
                channel_key = username or f"id{entity.id}"
                async for msg in client.iter_messages(entity, limit=limit):
                    text = msg.message
                    if not text:
                        continue
                    items.append(build_raw_item(
                        channel_key, username, entity.id, msg.id, text, msg.date))
            except Exception as exc:  # noqa: BLE001 — один канал не валит остальные
                log.warning("mtproto_channel_error", ref=value, error=str(exc))
    finally:
        await client.disconnect()
    return items


async def _collect_history(api_id: int, api_hash: str, session_path: str,
                           refs: list[tuple[str, str]], since: datetime,
                           until: datetime, max_per_channel: int) -> list[RawItem]:
    """Историч. сбор по окну дат: для каждого канала идём от until вглубь, пока не since.

    `iter_messages(offset_date=until)` отдаёт сообщения старше until, новейшие первыми;
    прерываемся, когда дата ушла раньше since. max_per_channel=0 — без лимита."""
    from telethon import TelegramClient
    from telethon.tl.functions.messages import (
        CheckChatInviteRequest,
        ImportChatInviteRequest,
    )
    from telethon.tl.types import ChatInviteAlready

    items: list[RawItem] = []
    client = TelegramClient(session_path, api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            log.warning("mtproto_not_authorized",
                        hint="запустите scripts/telegram_login.py")
            return items
        for kind, value in refs:
            try:
                entity = await _resolve(client, kind, value,
                                        CheckChatInviteRequest,
                                        ImportChatInviteRequest, ChatInviteAlready)
                if entity is None:
                    continue
                username = getattr(entity, "username", None)
                channel_key = username or f"id{entity.id}"
                count = 0
                async for msg in client.iter_messages(entity, offset_date=until):
                    if msg.date is not None and msg.date < since:
                        break
                    text = msg.message
                    if not text:
                        continue
                    items.append(build_raw_item(
                        channel_key, username, entity.id, msg.id, text, msg.date))
                    count += 1
                    if max_per_channel and count >= max_per_channel:
                        break
            except Exception as exc:  # noqa: BLE001 — один канал не валит остальные
                log.warning("mtproto_backfill_channel_error", ref=value, error=str(exc))
    finally:
        await client.disconnect()
    return items


async def _resolve(client, kind: str, value: str,
                   CheckChatInviteRequest, ImportChatInviteRequest, ChatInviteAlready):
    """Резолвит канал: @username напрямую; id<NN> — приватный по числовому ID
    (PeerChannel, фолбэк на диалоги для уже подписанных); инвайт — проверка членства."""
    if kind == "username":
        return await client.get_entity(value)
    if kind == "id":
        from telethon.tl.types import PeerChannel

        cid = int(value)
        try:
            return await client.get_entity(PeerChannel(cid))
        except Exception:  # noqa: BLE001 — нет в кэше access_hash: ищем среди диалогов
            async for dialog in client.iter_dialogs():
                if getattr(dialog.entity, "id", None) == cid:
                    return dialog.entity
            return None
    invite = await client(CheckChatInviteRequest(value))
    if isinstance(invite, ChatInviteAlready):
        return invite.chat                       # уже подписан
    updates = await client(ImportChatInviteRequest(value))   # вступаем
    chats = getattr(updates, "chats", None)
    return chats[0] if chats else None


@register
class TelegramMtprotoConnector(BaseConnector):
    """Закрытые Telegram-каналы через MTProto (Telethon, аккаунт-сессия)."""

    name = "telegram_mtproto"
    kind: SourceKind = SourceKind.NEWS

    def fetch(self) -> Iterable[RawItem]:
        import os

        s = get_settings()
        refs = parse_private_channels(s.telegram_private_channels or "")
        if not refs or not s.telegram_api_id or not s.telegram_api_hash:
            log.info("mtproto_skip", reason="нет каналов или кредов api_id/api_hash")
            return
        # До разового логина (scripts/telegram_login.py) файла-сессии нет — не дёргаем
        # сеть каждый цикл планировщика впустую.
        session_file = (s.telegram_session_path
                        if s.telegram_session_path.endswith(".session")
                        else f"{s.telegram_session_path}.session")
        if not os.path.exists(session_file):
            log.info("mtproto_skip", reason="нет файла-сессии — нужен telegram_login.py")
            return
        try:
            import asyncio

            items = asyncio.run(_collect(
                int(s.telegram_api_id), s.telegram_api_hash,
                s.telegram_session_path, refs, s.telegram_mtproto_limit,
            ))
        except ModuleNotFoundError:
            log.warning("mtproto_no_telethon",
                        hint="pip install -e .[mtproto]")
            return
        except Exception as exc:  # noqa: BLE001 — сеть/сессия не должны ронять цикл
            log.warning("mtproto_fetch_failed", error=str(exc))
            return
        yield from items


def backfill_channels(refs: list[tuple[str, str]], since: datetime,
                      until: datetime | None = None,
                      max_per_channel: int = 0) -> list[RawItem]:
    """Историч. бэкфилл новостей по окну дат (отдельно от планировщика, не `fetch`).

    Те же guard'ы, что и у коннектора (creds/файл-сессии/telethon) — при их отсутствии
    тихо возвращает []. until=None → текущий момент; max_per_channel=0 → без лимита."""
    import os

    s = get_settings()
    if until is None:
        until = datetime.now(tz=UTC)
    if not refs or not s.telegram_api_id or not s.telegram_api_hash:
        log.info("mtproto_backfill_skip", reason="нет каналов или кредов api_id/api_hash")
        return []
    session_file = (s.telegram_session_path
                    if s.telegram_session_path.endswith(".session")
                    else f"{s.telegram_session_path}.session")
    if not os.path.exists(session_file):
        log.info("mtproto_backfill_skip", reason="нет файла-сессии — нужен telegram_login.py")
        return []
    try:
        import asyncio

        return asyncio.run(_collect_history(
            int(s.telegram_api_id), s.telegram_api_hash,
            s.telegram_session_path, refs, since, until, max_per_channel,
        ))
    except ModuleNotFoundError:
        log.warning("mtproto_no_telethon", hint="pip install -e .[mtproto]")
        return []
    except Exception as exc:  # noqa: BLE001 — сеть/сессия не должны ронять процесс
        log.warning("mtproto_backfill_failed", error=str(exc))
        return []
