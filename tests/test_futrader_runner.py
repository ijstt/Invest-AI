"""Тесты торгового раннера (Трек 2, вынесен из scheduler — cozy-toasting-bunny.md, Трек A).

Проверяем управляющую логику `run_futrader_loop` без БД/сети: дневная петля раз в календарный
день, интрадей-цикл под гейтом сессии FORTS, watchdog при подряд-сбойных проходах. Дневная/интрадей
функции и `in_session` замоканы; цикл останавливаем через KeyboardInterrupt из `time.sleep`.
"""

from __future__ import annotations

import geoanalytics.futrader.session as sess
from geoanalytics.orchestration import futrader_runner as fr


def _stop_after(monkeypatch, n_ticks: int) -> None:
    """Подменяет time.sleep так, чтобы цикл прервался после n_ticks тиков (KeyboardInterrupt)."""
    state = {"n": 0}

    def sleeper(_seconds):
        state["n"] += 1
        if state["n"] >= n_ticks:
            raise KeyboardInterrupt

    monkeypatch.setattr(fr.time, "sleep", sleeper)


def test_loop_runs_daily_then_intraday_in_session(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(fr, "run_futrader_daily", lambda: calls.append("daily"))
    monkeypatch.setattr(fr, "run_futrader_intraday", lambda: calls.append("intraday"))
    monkeypatch.setattr(fr, "_intraday_due", lambda *a: True)
    monkeypatch.setattr(sess, "in_session", lambda *a, **k: True)
    _stop_after(monkeypatch, 1)

    fr.run_futrader_loop(interval=1)
    # Первый тик: смена дня → дневная петля, затем интрадей внутри сессии.
    assert calls == ["daily", "intraday"]


def test_loop_skips_intraday_outside_session(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(fr, "run_futrader_daily", lambda: calls.append("daily"))
    monkeypatch.setattr(fr, "run_futrader_intraday", lambda: calls.append("intraday"))
    monkeypatch.setattr(fr, "_intraday_due", lambda *a: True)
    monkeypatch.setattr(sess, "in_session", lambda *a, **k: False)  # сессия закрыта
    _stop_after(monkeypatch, 1)

    fr.run_futrader_loop(interval=1)
    assert calls == ["daily"]  # интрадей не вызван вне сессии


def test_loop_runs_daily_once_per_day(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(fr, "run_futrader_daily", lambda: calls.append("daily"))
    monkeypatch.setattr(fr, "run_futrader_intraday", lambda: None)
    monkeypatch.setattr(fr, "_intraday_due", lambda *a: False)
    monkeypatch.setattr(sess, "in_session", lambda *a, **k: False)
    _stop_after(monkeypatch, 2)  # два тика того же календарного дня

    fr.run_futrader_loop(interval=1)
    assert calls == ["daily"]  # дневная петля ровно один раз, несмотря на 2 тика


def test_loop_daily_failure_does_not_crash(monkeypatch):
    def boom():
        raise RuntimeError("accumulate down")

    monkeypatch.setattr(fr, "run_futrader_daily", boom)
    monkeypatch.setattr(fr, "run_futrader_intraday", lambda: None)
    monkeypatch.setattr(fr, "_intraday_due", lambda *a: False)
    monkeypatch.setattr(sess, "in_session", lambda *a, **k: False)
    _stop_after(monkeypatch, 1)

    # Сбой дневной петли изолирован _safe — демон не падает (исключение наружу не уходит).
    fr.run_futrader_loop(interval=1)


def test_loop_watchdog_after_consecutive_intraday_failures(monkeypatch):
    monkeypatch.setattr(fr, "run_futrader_daily", lambda: None)

    def boom():
        raise RuntimeError("paper cycle down")

    monkeypatch.setattr(fr, "run_futrader_intraday", boom)
    monkeypatch.setattr(fr, "_intraday_due", lambda *a: True)
    monkeypatch.setattr(sess, "in_session", lambda *a, **k: True)
    alerts: list[tuple[int, dict]] = []
    monkeypatch.setattr(fr, "_watchdog_alert", lambda n, **k: alerts.append((n, k)))
    _stop_after(monkeypatch, fr._WATCHDOG_THRESHOLD)

    fr.run_futrader_loop(interval=1)
    # После _WATCHDOG_THRESHOLD подряд-сбойных интрадей-проходов — ровно один алерт с СОБСТВЕННЫМ
    # дедупом (отличается от scheduler-watchdog), чтобы трейдер и scheduler алертили независимо.
    assert alerts and alerts[0][0] == fr._WATCHDOG_THRESHOLD
    assert alerts[0][1].get("dedup_prefix") == "futrader_watchdog"
