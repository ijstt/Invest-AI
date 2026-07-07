"""Входящий Telegram-бот (Волна 5a): long-poll getUpdates → роутер → ответ.

Отдельная служба `geo run-bot`. Read-only команды, single-user: отвечает только chat_id из
allowlist (`telegram_chat_id`). Сетевые/обрабатывающие сбои не валят демон (как scheduler):
каждый виток в предохранителе, плюс лёгкий per-chat rate-limit против залпа команд.
"""

from __future__ import annotations

import time

import httpx

from config.settings import get_settings
from geoanalytics.alerts.channels import send_telegram
from geoanalytics.bot import format as fmt
from geoanalytics.bot import identity
from geoanalytics.bot.router import dispatch, parse_command
from geoanalytics.core.logging import get_logger

log = get_logger("bot")

_API = "https://api.telegram.org/bot{token}/{method}"


def _get_updates(token: str, offset: int | None, timeout: int) -> list[dict]:
    """Long-poll getUpdates: Telegram держит соединение до `timeout` сек, пока нет апдейтов."""
    params: dict = {"timeout": timeout, "allowed_updates": '["message"]'}
    if offset is not None:
        params["offset"] = offset
    resp = httpx.get(_API.format(token=token, method="getUpdates"),
                     params=params, timeout=timeout + 10)
    resp.raise_for_status()
    return resp.json().get("result", [])


def _drain_offset(token: str) -> int | None:
    """Слить накопленные апдейты при старте, чтобы не отвечать на старые команды (offset)."""
    updates = _get_updates(token, None, 0)
    return updates[-1]["update_id"] + 1 if updates else None


def _handle_update(upd: dict, token: str,
                   last_seen: dict[str, float], rate_limit: float) -> None:
    """Обработать апдейт: /start-регистрация → авторизация по БД → rate-limit → роутер → ответ.

    Авторизация (5b) идёт через таблицу users (`identity`), а не статический allowlist.
    Сбои изолированы — демон не падает.
    """
    msg = upd.get("message") or {}
    text = msg.get("text")
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    frm = msg.get("from") or {}
    if not text or not chat_id:
        return
    cmd, arg = parse_command(text)

    # /start — регистрация/привязка; bootstrap уже сделал admin'ов разрешёнными.
    if cmd == "start":
        user = identity.register(int(frm.get("id") or chat_id), chat_id, frm.get("username"))
        send_telegram(token, chat_id, fmt.welcome(user) if user.allowed else fmt.pending())
        log.info("bot_start_cmd", chat_id=chat_id, allowed=user.allowed)
        return

    user = identity.authorize(chat_id)
    if user is None:
        send_telegram(token, chat_id, "Нет доступа. Отправьте /start для регистрации.")
        log.info("bot_unauthorized", chat_id=chat_id)
        return

    now = time.monotonic()
    if now - last_seen.get(chat_id, float("-inf")) < rate_limit:
        return
    last_seen[chat_id] = now
    try:
        reply = dispatch(cmd, arg, user=user)
    except Exception as exc:  # noqa: BLE001 — ошибка обработки команды не валит демон
        log.error("bot_dispatch_failed", cmd=cmd, error=str(exc))
        reply = "Не удалось обработать команду — попробуйте позже."
    send_telegram(token, chat_id, reply)
    log.info("bot_reply", chat_id=chat_id, cmd=cmd)


def run() -> None:
    """Бесконечный long-poll цикл бота. Тихо выходит, если бот выключен/нет токена."""
    settings = get_settings()
    token = settings.telegram_bot_token
    if not settings.telegram_bot_enabled or not token:
        log.warning("bot_disabled",
                    reason="нет токена или GEO_TELEGRAM_BOT_ENABLED=false")
        return
    timeout = settings.bot_poll_timeout_sec
    rate_limit = settings.bot_rate_limit_sec
    last_seen: dict[str, float] = {}     # chat_id → monotonic последней команды
    # 5b: разрешить admin'ов из стартового allowlist настроек (chat_id == user_id в личке).
    try:
        n_admins = identity.bootstrap_admins(settings.telegram_chat_ids)
    except Exception as exc:  # noqa: BLE001 — без БД bootstrap не валит старт
        n_admins = 0
        log.warning("bot_bootstrap_failed", error=str(exc))
    log.info("bot_start", admins=n_admins, timeout=timeout)

    offset: int | None = None
    try:
        offset = _drain_offset(token)
    except Exception as exc:  # noqa: BLE001 — старт без дренажа допустим
        log.warning("bot_drain_failed", error=str(exc))

    try:
        while True:
            try:
                updates = _get_updates(token, offset, timeout)
            except Exception as exc:  # noqa: BLE001 — сеть не валит демон
                log.warning("bot_poll_failed", error=str(exc))
                time.sleep(3)
                continue
            for upd in updates:
                offset = upd["update_id"] + 1
                _handle_update(upd, token, last_seen, rate_limit)
    except KeyboardInterrupt:
        log.info("bot_stop")
