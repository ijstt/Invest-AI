#!/usr/bin/env python
"""Разовый интерактивный логин Telegram MTProto (Telethon) → файл-сессия.

Два способа (оба — ОДИН раз, в обычном терминале):

  ВХОД ПО QR (рекомендуется — обходит SMS/код целиком):
      cd /home/ijstt/News && .venv/bin/python scripts/telegram_login.py --qr
  Открой Telegram на телефоне → Настройки → Устройства → «Подключить устройство»
  и наведи камеру на QR в терминале. Если включён 2FA — спросит облачный пароль.

  ВХОД ПО НОМЕРУ И КОДУ:
      cd /home/ijstt/News && .venv/bin/python scripts/telegram_login.py
  Код приходит СООБЩЕНИЕМ в чат «Telegram» в приложении (не SMS!), если есть другая
  активная сессия. ВАЖНО: код запрашивается ОДИН раз — повторные запросы быстро упирают
  Telegram в SendCodeUnavailableError («исчерпаны способы доставки»). Если это случилось —
  подожди 1–24 ч или используй --qr.

Нужны в .env: GEO_TELEGRAM_API_ID, GEO_TELEGRAM_API_HASH (с my.telegram.org).
Можно заранее задать GEO_TELEGRAM_PHONE=+7...
После успеха создаётся файл-сессия (settings.telegram_session_path), которым пользуется
коннектор telegram_mtproto и бэкфилл (geo news-backfill).

Зависимость: pip install -e .[mtproto]  (telethon + qrcode)
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

# Запускается напрямую (без PYTHONPATH) — добавляем корень репозитория в путь.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings  # noqa: E402


def _print_qr(url: str) -> None:
    """Рисует QR ссылки логина в терминале (ASCII). Без qrcode — печатает URL."""
    try:
        import qrcode

        qr = qrcode.QRCode(border=2)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception:  # noqa: BLE001 — нет qrcode/терминал не тянет: печатаем URL
        print("(qrcode недоступен — открой ссылку как QR вручную)")
    print(f"\n  {url}\n")


async def _run_qr(s) -> int:
    """Логин по QR: показываем QR, ждём сканирования с телефона, при 2FA — пароль."""
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError

    client = TelegramClient(s.telegram_session_path, int(s.telegram_api_id),
                            s.telegram_api_hash)
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print("Уже авторизован:", getattr(me, "username", None) or me.first_name)
            return 0

        qr = await client.qr_login()
        print("Открой Telegram на телефоне → Настройки → Устройства →")
        print("«Подключить устройство» и наведи камеру на QR ниже.\n")
        while True:
            _print_qr(qr.url)
            try:
                await qr.wait(timeout=40)
                break
            except TimeoutError:
                await qr.recreate()
                print("QR истёк — обновлён, сканируй заново…\n")
            except SessionPasswordNeededError:
                pw = getpass.getpass("Включён 2FA — облачный пароль: ")
                await client.sign_in(password=pw)
                break

        me = await client.get_me()
        uname = getattr(me, "username", None) or getattr(me, "first_name", "?")
        print(f"\nАвторизация успешна: {uname}")
        print(f"Файл-сессия: {s.telegram_session_path}")
        return 0
    finally:
        await client.disconnect()


def _login_by_code(s) -> int:
    """Логин по номеру и коду. Код запрашивается ОДИН раз (без авто-пересылки)."""
    # ВАЖНО: telethon.sync — иначе connect()/send_code_request()/sign_in() возвращают
    # корутины и «молча ждут код, который не идёт».
    from telethon.errors import FloodWaitError, SessionPasswordNeededError
    from telethon.errors.rpcerrorlist import (
        PhoneCodeExpiredError,
        PhoneCodeInvalidError,
        SendCodeUnavailableError,
    )
    from telethon.sync import TelegramClient

    if not sys.stdin.isatty():
        print(
            "Нужен ИНТЕРАКТИВНЫЙ терминал: открой обычное окно терминала и запусти\n"
            "    cd /home/ijstt/News && .venv/bin/python scripts/telegram_login.py\n"
            "(внутри агента stdin закрыт — ввести телефон/код нельзя).",
            file=sys.stderr,
        )
        return 1

    client = TelegramClient(s.telegram_session_path, int(s.telegram_api_id),
                            s.telegram_api_hash)
    client.connect()
    try:
        if client.is_user_authorized():
            me = client.get_me()
            print("Уже авторизован:", getattr(me, "username", None) or me.first_name)
            return 0

        phone = os.environ.get("GEO_TELEGRAM_PHONE") or input(
            "Номер телефона (+7...): ").strip()

        try:
            sent = client.send_code_request(phone)
        except FloodWaitError as exc:
            print(f"Telegram просит подождать {exc.seconds} сек перед запросом кода.\n"
                  "Подожди и повтори — либо войди по QR: scripts/telegram_login.py --qr",
                  file=sys.stderr)
            return 1
        except SendCodeUnavailableError:
            print("Telegram исчерпал способы доставки кода (слишком много запросов).\n"
                  "Подожди 1–24 ч и попробуй СНОВА ОДИН раз, либо войди по QR:\n"
                  "    .venv/bin/python scripts/telegram_login.py --qr",
                  file=sys.stderr)
            return 1

        cur = type(sent.type).__name__.replace("SentCodeType", "")
        nxt = type(sent.next_type).__name__.replace("SentCodeType", "") if sent.next_type else "—"
        print(f"\nTelegram отправил код способом: {cur}  (запасной способ: {nxt})")
        print("  App  — код придёт СООБЩЕНИЕМ в чат «Telegram» в приложении (не SMS).")
        print("  Sms  — код придёт по SMS.   Call — продиктуют звонком.")
        print("Если кода нет — НЕ спамь запросами: введи 'sms'/'call' один раз, либо --qr.\n")

        while True:
            code = input("Код (Enter — ждать ещё; 'sms'/'call' — сменить способ): ").strip()
            low = code.lower()
            if low in ("sms", "call"):
                try:
                    sent = client.send_code_request(phone, force_sms=(low == "sms"))
                except (FloodWaitError, SendCodeUnavailableError) as exc:
                    print(f"Сменить способ нельзя ({type(exc).__name__}). "
                          "Подожди или войди по QR (--qr).\n")
                    continue
                print(f"Запрошено способом: "
                      f"{type(sent.type).__name__.replace('SentCodeType', '')}\n")
                continue
            if not code:
                print("Жду код (повторно НЕ запрашиваю — чтобы не упереться в лимит).\n")
                continue
            try:
                client.sign_in(phone, code)
                break
            except SessionPasswordNeededError:
                pw = getpass.getpass("Включён 2FA — пароль облака: ")
                client.sign_in(password=pw)
                break
            except (PhoneCodeInvalidError, PhoneCodeExpiredError) as exc:
                print(f"Код не подошёл ({type(exc).__name__}). Попробуй ещё раз "
                      "(или 'sms'/'call').\n")

        me = client.get_me()
        uname = getattr(me, "username", None) or getattr(me, "first_name", "?")
        print(f"\nАвторизация успешна: {uname}")
        print(f"Файл-сессия: {s.telegram_session_path}")
        return 0
    finally:
        client.disconnect()


def main() -> int:
    s = get_settings()
    if not s.telegram_api_id or not s.telegram_api_hash:
        print("Нет GEO_TELEGRAM_API_ID / GEO_TELEGRAM_API_HASH в .env", file=sys.stderr)
        return 1

    if "--qr" in sys.argv[1:]:
        import asyncio

        try:
            return asyncio.run(_run_qr(s))
        except ModuleNotFoundError:
            print("Не установлен telethon: pip install -e .[mtproto]", file=sys.stderr)
            return 1

    try:
        return _login_by_code(s)
    except ModuleNotFoundError:
        print("Не установлен telethon: pip install -e .[mtproto]", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
