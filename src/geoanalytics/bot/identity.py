"""Идентичность и авторизация пользователей бота (Волна 5b).

Тонкие session-обёртки над `UserRepository`: регистрация по /start, авторизация по chat_id,
bootstrap admin'ов из стартового allowlist настроек. Возвращают снимок `UserInfo` (не ORM-
объект), чтобы вызывающий не держал сессию открытой.
"""

from __future__ import annotations

from dataclasses import dataclass

from geoanalytics.storage.db import session_scope
from geoanalytics.storage.repositories import UserRepository


@dataclass(frozen=True)
class UserInfo:
    id: int
    telegram_user_id: int
    chat_id: str
    username: str | None
    role: str
    allowed: bool


def _snap(u) -> UserInfo:
    return UserInfo(u.id, u.telegram_user_id, u.chat_id, u.username, u.role, u.allowed)


def register(telegram_user_id: int, chat_id: str, username: str | None = None) -> UserInfo:
    """Зарегистрировать/обновить пользователя по /start (allowed не меняет)."""
    with session_scope() as session:
        return _snap(UserRepository(session).register(telegram_user_id, chat_id, username))


def authorize(chat_id: str) -> UserInfo | None:
    """Снимок разрешённого пользователя по chat_id или None (нет/не авторизован)."""
    with session_scope() as session:
        u = UserRepository(session).get_by_chat_id(chat_id)
        return _snap(u) if (u and u.allowed) else None


def bootstrap_admins(chat_ids: list[str]) -> int:
    """Гарантировать admin'ов из стартового allowlist. В личке chat_id == telegram_user_id."""
    n = 0
    with session_scope() as session:
        repo = UserRepository(session)
        for cid in chat_ids:
            try:
                tg = int(cid)
            except (TypeError, ValueError):
                continue
            repo.ensure_admin(tg, str(cid))
            n += 1
    return n
