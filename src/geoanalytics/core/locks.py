"""Межпроцессные замки через Postgres advisory locks.

Бот (`geo-bot`) и дашборд (`geo-dashboard`) — РАЗНЫЕ процессы на одной БД, поэтому in-process
threading.Lock не спасает от перекрёстного вызова. Тяжёлая LLM-генерация (Ollama 3B/7B) на
слабом железе НЕ должна идти ДВУМЯ запросами одновременно (OOM/контеншн). Берём неблокирующий
`pg_try_advisory_xact_lock`: занят → `LLMBusy` (вызывающий деградирует на ответ без ИИ).

Замок транзакционный — снимается автоматически при завершении транзакции (commit/rollback/закрытие
соединения), т.е. переживает падение процесса. Fail-OPEN: сбой БД не должен ронять ответы.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from geoanalytics.core.logging import get_logger

log = get_logger("core.locks")

# Стабильный ключ advisory-лока LLM-генерации (фикс. целое; общий для бота и дашборда).
LLM_GENERATION_LOCK_KEY = 770_111_001


class LLMBusy(RuntimeError):
    """LLM-генерация уже идёт в другом запросе/процессе (бот↔дашборд) — второй заблокирован."""


@contextlib.contextmanager
def llm_generation_lock() -> Iterator[None]:
    """Неблокирующий межпроцессный замок LLM-генерации.

    Занят другим запросом → `LLMBusy`. Сбой БД (нет коннекта) → fail-open (пускаем без замка, лог).
    Замок держится на время блока `with` (на нём идёт генерация) и снимается с концом транзакции.
    """
    from sqlalchemy import text

    from geoanalytics.storage.db import session_scope

    with contextlib.ExitStack() as stack:
        try:
            session = stack.enter_context(session_scope())
            got = session.execute(
                text("SELECT pg_try_advisory_xact_lock(:k)"),
                {"k": LLM_GENERATION_LOCK_KEY},
            ).scalar()
        except Exception as exc:  # noqa: BLE001 — fail-open: БД недоступна → не блокируем ответ
            log.warning("llm_lock_unavailable", error=str(exc))
            yield
            return
        if not got:
            log.info("llm_lock_busy")
            raise LLMBusy()
        yield
