"""Общие примитивы оркестрации, разделяемые scheduler'ом и торговым раннером (futrader_runner).

Вынесены сюда, чтобы торговый процесс (Трек 2, чисто numeric, Raspberry-Pi-ready) НЕ импортировал
тяжёлый модуль `scheduler` (тот тянет коннекторы/NLP-смежное). Общие для обоих демонов:
изоляция сбоя этапа (`_safe`), проверка каданса интрадей-цикла (`_intraday_due`) и
watchdog-алерт о подряд-сбойных циклах (`_watchdog_alert`)."""

from __future__ import annotations

from datetime import UTC, datetime

from geoanalytics.core.logging import get_logger

log = get_logger("scheduler")

# Б15: число подряд-сбойных циклов, после которого демон шлёт алерт о нестабильности.
_WATCHDOG_THRESHOLD = 3


def _intraday_due(now_mono: float, last_mono: float, interval_sec: int) -> bool:
    """Истёк ли интервал интрадей-цикла трейдера (Трек 2 / Фаза B). `interval_sec<=0` — выключено.
    Чистая (тестируемая) проверка каданса; гейт сессии FORTS — в caller (`in_session`)."""
    return interval_sec > 0 and (now_mono - last_mono) >= interval_sec


def _safe(stage: str, fn, *args, **kwargs) -> tuple[object, bool]:
    """Выполняет этап цикла, изолируя сбой (Б15): возвращает (результат, ok).

    Один упавший этап (сетевой сбой коннектора, хиккап БД) логируется и НЕ валит ни
    остальные этапы цикла, ни демон. ok=False сигнализирует watchdog'у о проблеме.
    """
    try:
        return fn(*args, **kwargs), True
    except Exception as exc:  # noqa: BLE001 — Б15: этап не валит цикл
        log.error(f"scheduler_{stage}_failed", error=str(exc))
        return None, False


def _watchdog_alert(consecutive: int, *, title: str = "Scheduler нестабилен",
                    message: str | None = None,
                    dedup_prefix: str = "scheduler_watchdog") -> None:
    """Telegram-алерт о подряд-сбойных циклах демона (дедуп по часу, как у health).

    Параметризован title/message/dedup_prefix, чтобы scheduler и торговый раннер слали
    РАЗЛИЧИМЫЕ алерты с независимым дедупом (дефолты сохраняют поведение scheduler)."""
    from config.settings import get_settings
    from geoanalytics.alerts import channels
    from geoanalytics.alerts.engine import _insert_new
    from geoanalytics.alerts.rules import Alert
    from geoanalytics.storage.db import session_scope

    bucket = datetime.now(UTC).strftime("%Y-%m-%d-%H")
    msg = message or (f"Цикл сбора падает {consecutive} раз(а) подряд — "
                      "проверьте логи scheduler.")
    alert = Alert(
        alert_type="health",
        severity="critical",
        title=title,
        message=msg,
        dedup_key=f"{dedup_prefix}:{bucket}",
        payload={"consecutive_failures": consecutive},
    )
    try:
        with session_scope() as session:
            rec_id = _insert_new(session, alert)
        if rec_id is not None:
            channels.dispatch(alert, get_settings())
    except Exception as exc:  # noqa: BLE001 — без БД/канала остаётся хотя бы лог
        log.error("scheduler_watchdog_alert_failed", error=str(exc))
