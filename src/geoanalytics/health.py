"""Health-check фолбэков каскада (I4, Волна 1 роудмапа v2.0).

Каскад построен на graceful degradation: модель не загрузилась → тихий фолбэк
(формула/правила/лексикон), процесс живёт. Для качества это мина (Б1/Б17):
распределения скоров молча меняются, а гейты алертов откалиброваны под модели.
Этот модуль делает деградацию ГРОМКОЙ:

- `check()`  — статусы всех компонентов каскада (модели, БД, LLM);
- `report()` — лог-сводка одной строкой + Telegram-алерт по каждому
  деградировавшему компоненту (дедуп через таблицу alerts: раз в день
  на компонент, повторные прогоны не спамят).

Вызывается при старте scheduler, в nightly-блоке и руками: `geo health`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from geoanalytics.core.logging import get_logger

log = get_logger("health")

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"


@dataclass(frozen=True)
class ComponentHealth:
    """Статус одного компонента каскада."""

    name: str
    status: str   # ok | degraded
    detail: str


def _db_status() -> tuple[str, str]:
    """Доступность БД (SELECT 1)."""
    from sqlalchemy import text

    from geoanalytics.storage.db import get_engine

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return STATUS_OK, "postgres доступен"
    except Exception as exc:  # noqa: BLE001 — статус, не падение
        return STATUS_DEGRADED, f"БД недоступна: {exc}"


def _llm_status() -> tuple[str, str]:
    """Доступность LLM (Ollama ping / наличие cloud-ключа)."""
    from geoanalytics.nlp import llm

    if llm.is_available():
        return STATUS_OK, "LLM доступен"
    return STATUS_DEGRADED, "LLM недоступен — ask-путь/сводки деградируют до правил"


def _orphan_assets_status() -> tuple[str, str]:
    """Акции без company_id (мина Б9): не попадают в секторные скоупы и линковку.

    Такие активы появляются, когда обработка видит тикер не из сида и заводит
    его на лету. Лечится добавлением эмитента в seed.ISSUERS + `geo db seed`."""
    from sqlalchemy import func, select

    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset

    with session_scope() as session:
        orphans = list(session.scalars(
            select(Asset.ticker).where(Asset.kind == "share",
                                       Asset.company_id.is_(None))
            .order_by(Asset.ticker).limit(10)
        ))
        if not orphans:
            return STATUS_OK, "у всех акций есть компания/сектор"
        total = session.scalar(
            select(func.count()).select_from(Asset)
            .where(Asset.kind == "share", Asset.company_id.is_(None))
        ) or len(orphans)
    return STATUS_DEGRADED, (
        f"акций без компании: {total} ({', '.join(orphans)}"
        f"{'…' if total > len(orphans) else ''}) — вне секторных скоупов, "
        "добавьте в seed.ISSUERS"
    )


def _sig_gates_status() -> tuple[str, str]:
    """Б6: инвариант каскада порогов значимости (инжест ≤ алерт, выравнивание с бакетами)."""
    from geoanalytics.nlp.significance import significance_gates, validate_cascade

    problems = validate_cascade()
    if problems:
        return STATUS_DEGRADED, "; ".join(problems)
    g = significance_gates()
    return STATUS_OK, f"каскад согласован (инжест {g['ingest']} ≤ алерт {g['alert']})"


def check() -> list[ComponentHealth]:
    """Собирает статусы всех компонентов. Ленивые загрузчики моделей дергаются
    по-настоящему (это и есть смысл проверки), ошибка одного компонента не валит
    остальные."""
    from geoanalytics.nlp import (
        aspect,
        classify,
        embeddings,
        ner,
        sentiment,
        significance,
        temporal,
    )

    checks = [
        ("db", _db_status),
        ("sentiment", sentiment.model_status),
        ("events", classify.model_status),
        ("significance", significance.model_status),
        ("sig_gates", _sig_gates_status),
        ("aspect", aspect.model_status),
        ("temporal", temporal.model_status),
        ("embedder", embeddings.model_status),
        ("ner", ner.model_status),
        ("llm", _llm_status),
        ("assets", _orphan_assets_status),
    ]
    out: list[ComponentHealth] = []
    for name, fn in checks:
        try:
            status, detail = fn()
        except Exception as exc:  # noqa: BLE001 — сама проверка не должна падать
            status, detail = STATUS_DEGRADED, f"проверка упала: {exc}"
        out.append(ComponentHealth(name=name, status=status, detail=detail))
    return out


def degraded(components: list[ComponentHealth]) -> list[ComponentHealth]:
    """Только деградировавшие компоненты."""
    return [c for c in components if c.status != STATUS_OK]


def _dispatch_degraded_alerts(bad: list[ComponentHealth]) -> int:
    """Алерт по каждому деградировавшему компоненту через стандартный путь алертов.

    Дедуп — `health:{component}:{день}` в таблице alerts: за день по компоненту уходит
    максимум одно уведомление, сколько бы раз ни вызывался report(). Возвращает число
    реально отправленных (новых) алертов.
    """
    from config.settings import get_settings
    from geoanalytics.alerts import channels
    from geoanalytics.alerts.engine import _insert_new
    from geoanalytics.alerts.rules import Alert
    from geoanalytics.storage.db import session_scope

    settings = get_settings()
    bucket = datetime.now(UTC).strftime("%Y-%m-%d")
    sent = 0
    for comp in bad:
        alert = Alert(
            alert_type="health",
            severity="critical",
            title=f"Деградация каскада: {comp.name}",
            message=f"Health-check: компонент «{comp.name}» деградировал. {comp.detail}.",
            dedup_key=f"health:{comp.name}:{bucket}",
            payload={"component": comp.name, "detail": comp.detail},
        )
        try:
            with session_scope() as session:
                rec_id = _insert_new(session, alert)
            if rec_id is not None:
                channels.dispatch(alert, settings)
                sent += 1
        except Exception as exc:  # noqa: BLE001 — без БД остаётся хотя бы лог
            log.error("health_alert_failed", component=comp.name, error=str(exc))
    return sent


def report(send_alerts: bool = True) -> list[ComponentHealth]:
    """Полный health-репорт: проверка + лог-сводка + (опц.) алерты о деградации.

    Никогда не бросает исключений — безопасно звать из scheduler-цикла.
    """
    try:
        components = check()
    except Exception as exc:  # noqa: BLE001 — health не должен валить вызывающего
        log.error("health_check_failed", error=str(exc))
        return []
    bad = degraded(components)
    summary = {c.name: c.status for c in components}
    if bad:
        log.error("health_degraded", components=summary,
                  details={c.name: c.detail for c in bad})
        if send_alerts:
            # Ожидаемо-офлайн компоненты (напр. embedder/llm на Pi, Фаза 2) логируем, но НЕ алертим.
            from config.settings import get_settings
            expected = get_settings().health_expected_offline_set
            alertable = [c for c in bad if c.name not in expected]
            if alertable:
                _dispatch_degraded_alerts(alertable)
    else:
        log.info("health_ok", components=summary)
    return components
