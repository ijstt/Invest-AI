"""Тест чистой логики планировщика (`due_sources`) — раздельная частота источников (Фаза D).

DB/сеть не задействованы: проверяем только, кого пора собирать при данном `last_run`/`now`.
Б15: изоляция сбоев этапов цикла (`_safe`/`_run_cycle`) — стейджи замоканы, демон не падает.
"""

from __future__ import annotations

from types import SimpleNamespace

from geoanalytics.core.types import SourceKind
from geoanalytics.orchestration import scheduler
from geoanalytics.orchestration.scheduler import _run_cycle, _safe, due_sources


class _Conn:
    def __init__(self, name: str, kind: SourceKind):
        self.name = name
        self.kind = kind


_CONNS = [
    _Conn("moex", SourceKind.MARKET),
    _Conn("interfax", SourceKind.NEWS),
    _Conn("cbr", SourceKind.MACRO),
]
_INTERVALS = {SourceKind.MARKET: 300, SourceKind.NEWS: 900, SourceKind.MACRO: 86400}


def test_first_tick_collects_all():
    """Пустой last_run → все источники просрочены (полный прогон при старте)."""
    due = due_sources(_CONNS, {}, now=1000.0, intervals=_INTERVALS)
    assert set(due) == {"moex", "interfax", "cbr"}


def test_market_due_every_tick_but_news_and_macro_not_yet():
    now = 10_000.0
    last_run = {"moex": now - 300, "interfax": now - 300, "cbr": now - 300}
    due = due_sources(_CONNS, last_run, now=now, intervals=_INTERVALS)
    assert due == ["moex"]  # market 300с истёк; news(900)/macro(86400) — нет


def test_news_due_after_its_interval():
    now = 10_000.0
    last_run = {"moex": now - 300, "interfax": now - 900, "cbr": now - 300}
    due = due_sources(_CONNS, last_run, now=now, intervals=_INTERVALS)
    assert set(due) == {"moex", "interfax"}  # macro всё ещё не пора


def test_unknown_kind_skipped():
    conns = [_Conn("weird", "unknown")]  # type: ignore[arg-type]
    assert due_sources(conns, {}, now=1.0, intervals=_INTERVALS) == []


def test_intraday_due_respects_interval_and_disable():
    from geoanalytics.orchestration.scheduler import _intraday_due
    assert _intraday_due(1000.0, 0.0, 600) is True       # прошло 1000с ≥ 600
    assert _intraday_due(500.0, 0.0, 600) is False        # ещё не пора
    assert _intraday_due(1200.0, 600.0, 600) is True      # ровно по границе (600с)
    assert _intraday_due(1000.0, 0.0, 0) is False         # 0 — интрадей-цикл выключен


# --------------------------------------------------------------------------- #
# Б15: изоляция сбоев этапов цикла.
# --------------------------------------------------------------------------- #
def test_safe_returns_result_and_ok():
    assert _safe("x", lambda: 42) == (42, True)


def test_safe_isolates_exception():
    def boom():
        raise RuntimeError("network down")

    result, ok = _safe("x", boom)
    assert result is None
    assert ok is False


def _patch_stages(monkeypatch, *, failing: set[str] = frozenset()):
    """Замокать все этапы _run_cycle; имена из `failing` бросают исключение.

    Возвращает список вызванных этапов (порядок проверяет, что сбой не прерывает цикл)."""
    calls: list[str] = []

    def stage(name, ret):
        def fn(*_a, **_k):
            calls.append(name)
            if name in failing:
                raise RuntimeError(f"{name} failed")
            return ret
        return fn

    monkeypatch.setattr(scheduler, "all_connectors", lambda: list(_CONNS))
    monkeypatch.setattr(scheduler, "ingest_source", stage("ingest", SimpleNamespace(stored=1)))
    monkeypatch.setattr(scheduler, "process_pending", stage("process", SimpleNamespace(articles=2)))
    monkeypatch.setattr(scheduler, "assign_stories", stage("stories", None))
    monkeypatch.setattr(scheduler, "build_events", stage("events", 3))
    monkeypatch.setattr(scheduler, "evaluate_and_dispatch",
                        stage("alerts", SimpleNamespace(created=0)))
    return calls


def test_run_cycle_all_ok(monkeypatch):
    calls = _patch_stages(monkeypatch)
    last_run: dict[str, float] = {}
    assert _run_cycle(last_run, _INTERVALS) is True
    # На пустом last_run собираются все источники, затем общие этапы.
    assert {"ingest", "process", "stories", "events", "alerts"} <= set(calls)
    assert set(last_run) == {"moex", "interfax", "cbr"}  # каденс обновлён


def test_run_cycle_continues_after_stage_failure(monkeypatch):
    # Падает обработка — последующие этапы всё равно выполняются, cycle_ok=False.
    calls = _patch_stages(monkeypatch, failing={"process"})
    assert _run_cycle({}, _INTERVALS) is False
    assert "events" in calls and "alerts" in calls  # цикл не прервался на сбое process


def test_run_cycle_keeps_cadence_on_ingest_failure(monkeypatch):
    # Сбой ингеста источника не мешает обновить last_run (ретрай по своему интервалу).
    _patch_stages(monkeypatch, failing={"ingest"})
    last_run: dict[str, float] = {}
    assert _run_cycle(last_run, _INTERVALS) is False
    assert set(last_run) == {"moex", "interfax", "cbr"}
