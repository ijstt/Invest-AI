"""Трек 2: автономный торговый демон (futrader) — отдельный процесс от scheduler.

Чисто numeric (sklearn/индикаторы), без GPU/LLM/Ollama. Содержит:
  - интрадей-цикл бумажной торговли (вход/держать/флэт к закрытию) ВНУТРИ сессии FORTS;
  - ДНЕВНУЮ петлю самообучения (accumulate → train → walk-forward eval → paper) + еженедельные
    PBO/drift.

Читает только готовые `market_regimes`/`market_sentiment` из общего Postgres (их пишет scheduler),
а пишет лишь в `futures_*` таблицы → Raspberry-Pi-ready: переносится на Pi указанием `GEO_DB_HOST`
на главную машину без единой правки кода. Точка входа — `geo run-futrader`.

Вынесено из scheduler (cozy-toasting-bunny.md, Трек A): дневная futrader-петля давала ~8G-пик
памяти монолитного scheduler; теперь это отдельная служба `geo-futrader` со своим потолком.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from config.settings import get_settings
from geoanalytics.core.logging import get_logger
from geoanalytics.orchestration.runtime import (
    _WATCHDOG_THRESHOLD,
    _intraday_due,
    _safe,
    _watchdog_alert,
)

log = get_logger("futrader")


def _refresh_intraday_candles(interval: str) -> int:
    """Докачивает свежие интрадей-свечи фронт-контрактов ПЕРЕД паперингом. Возвращает число свечей.

    Зачем: интрадей-цикл скорит «последний бар из БД», но сам бэкфилл свечей раньше шёл ТОЛЬКО в
    дневной петле (раз в сутки). В течение сессии бар устаревал → stale-гейт (`entry_bar_too_stale`)
    рубил ВСЕ входы (поймано 2026-06-29: бары 20ч, opened=0, blocked_stale=23). Теперь каждый тик
    тянем свежее окно (days=2 — мост через ночь/выходные) для торгуемого набора. Сбой ISS по одному
    инструменту изолирован и не мешает паперингу на остальных. MOEX ISS с Pi доступен (не
    блокируется, в отличие от Telegram) — прокси не нужен."""
    from geoanalytics.futrader.accumulate import DEFAULT_TICKERS
    from geoanalytics.futrader.data import backfill_futures_intraday
    from geoanalytics.storage.db import session_scope

    added = 0
    for ticker in DEFAULT_TICKERS:
        try:
            with session_scope() as session:
                added += backfill_futures_intraday(session, ticker, interval=interval, days=2)
        except Exception as exc:  # noqa: BLE001 — сеть/ISS по одному инструменту не валит цикл
            log.warning("futrader_intraday_refresh_failed", ticker=ticker, error=str(exc))
    return added


def run_futrader_intraday() -> None:
    """Бумажный интрадей-цикл на торговом ТФ ВНУТРИ сессии FORTS — оперативная реакция (вход /
    держать / флэт к закрытию), отдельно от ДНЕВНОЙ петли (та копит/обучает/переоценивает раз в
    день). Сперва докачивает свежие свечи (иначе stale-гейт рубит входы на залежавшемся баре), затем
    `run_paper_cycle` (грузит чемпионов + скорит бар). Чистый numeric, Ollama не трогает."""
    from geoanalytics.futrader.paper import run_paper_cycle
    from geoanalytics.storage.db import session_scope

    interval = get_settings().futrader_intraday_interval
    refreshed = _refresh_intraday_candles(interval)
    with session_scope() as session:
        paper = run_paper_cycle(session, interval=interval)
    log.info("futrader_intraday", interval=interval, refreshed=refreshed, opened=paper.opened,
             closed=paper.closed, session_flat=paper.session_flat,
             blocked_session=paper.blocked_session, equity=paper.equity)


def run_futrader_daily() -> None:
    """Дневная петля самообучения форка. Каждый шаг в своём try — сбой одного не валит остальные
    (раннер автономен, поэтому страхуемся локально, как _daily_jobs scheduler'а)."""
    try:
        # Фаза 0: накопление пулинг-датасета фьючерсного форка — бэкфилл всех инструментов FORTS
        # (час/день) по контрактам + лог решений всех стратегий → futures_decisions. Узкое место
        # Трека 2 — глубина данных; копим её непрерывно. Сбой инструмента изолирован внутри.
        from geoanalytics.futrader.accumulate import accumulate_dataset
        from geoanalytics.storage.db import session_scope

        with session_scope() as session:
            r = accumulate_dataset(session)
        log.info("futrader_accumulate", candles=r.candles,
                 decisions=r.decisions, labeled=r.labeled)
    except Exception as exc:  # noqa: BLE001
        log.error("futrader_accumulate_failed", error=str(exc))
    try:
        # Фаза B+C+D: ПЕТЛЯ САМООБУЧЕНИЯ. На дозревших данных: переобучить пулинг-политики,
        # walk-forward переоценить + промоутить чемпиона (реестр), затем бумажный цикл (квалиф.
        # чемпионы торгуют на демо-счёте под гейтом качества + vol-target + брейкером).
        from geoanalytics.futrader.decisions import SIGNAL_FNS
        from geoanalytics.futrader.evaluation import evaluate_and_record
        from geoanalytics.futrader.paper import run_paper_cycle
        from geoanalytics.futrader.policy import train_policy
        from geoanalytics.futrader.signals import CROSS_SECTIONAL
        from geoanalytics.storage.db import session_scope

        # Фаза B: основной торговый ТФ — интрадей (10m); 1h держим в реестре для сравнения.
        # Дневной paper папёрит на торговом ТФ; внутрисессионную реакцию даёт интрадей-цикл.
        trade_interval = get_settings().futrader_intraday_interval
        eval_intervals = (trade_interval, "1h")
        strategies = list(SIGNAL_FNS) + list(CROSS_SECTIONAL)   # +кросс-секция (Пул 9/E) под оценку
        n_trials = len(strategies)
        with session_scope() as session:
            for strat in strategies:
                try:
                    train_policy(session, source=strat, asset_code=None)
                    for iv in eval_intervals:
                        evaluate_and_record(session, source=strat, asset_code=None,
                                            interval=iv, n_trials=n_trials)
                except Exception as exc:  # noqa: BLE001 — одна стратегия не валит петлю
                    log.warning("futrader_eval_skip", strategy=strat, error=str(exc))
            paper = run_paper_cycle(session, interval=trade_interval)
        log.info("futrader_loop", opened=paper.opened, closed=paper.closed,
                 session_flat=paper.session_flat, qualified=paper.qualified_strategies,
                 equity=paper.equity)
    except Exception as exc:  # noqa: BLE001
        log.error("futrader_loop_failed", error=str(exc))
    if datetime.now(UTC).isoweekday() == 1:
        try:
            # Пул 4: еженедельный мониторинг «доказанности» — PBO (вероятность переобучения
            # бэктеста) по стратегиям. Растущий PBO сигналит, что отбор чемпиона ловит шум.
            from geoanalytics.futrader.decisions import SIGNAL_FNS
            from geoanalytics.futrader.evaluation import run_cpcv_pbo
            from geoanalytics.futrader.signals import CROSS_SECTIONAL
            from geoanalytics.storage.db import session_scope

            with session_scope() as session:
                pbo = run_cpcv_pbo(session, sources=list(SIGNAL_FNS) + list(CROSS_SECTIONAL),
                                   interval=get_settings().futrader_intraday_interval)
            log.info("futrader_pbo", pbo=pbo.pbo, n_folds=pbo.n_folds, configs=pbo.configs)
        except Exception as exc:  # noqa: BLE001
            log.error("futrader_pbo_failed", error=str(exc))
        try:
            # Пул 9/D: live-дрейф торгуемого чемпиона (PSI/калибровка/decay); жёсткий дрейф → halt.
            from geoanalytics.futrader.decisions import SIGNAL_FNS
            from geoanalytics.futrader.monitoring import run_drift_monitor
            from geoanalytics.storage.db import session_scope

            with session_scope() as session:
                reports = run_drift_monitor(session, sources=list(SIGNAL_FNS),
                                            interval=get_settings().futrader_intraday_interval)
            halted = [r.source for r in reports if r.should_halt]
            log.info("futrader_drift", checked=len(reports), halted=halted)
        except Exception as exc:  # noqa: BLE001
            log.error("futrader_drift_failed", error=str(exc))


def run_futrader_loop(interval: int | None = None) -> None:
    """Бесконечный торговый цикл (Трек 2): дневная петля при смене календарного дня + интрадей-цикл
    на своём кадансе внутри сессии FORTS.

    `interval` (сек) — базовый тик, по умолчанию `GEO_FUTRADER_INTRADAY_INTERVAL_SEC` (10m). Б15:
    ни один сбой (этапа/дня/интрадея) не роняет демон — всё логируется и цикл продолжается; при
    `_WATCHDOG_THRESHOLD` подряд-сбойных проходах уходит Telegram-алерт (отдельный дедуп от
    scheduler). datetime.now() наивное = MSK-настенное (свечи MOEX так и хранятся)."""
    settings = get_settings()
    intraday_sec = settings.futrader_intraday_interval_sec
    if interval is None:
        interval = intraday_sec if intraday_sec > 0 else 60
    log.info("futrader_loop_start", interval=interval, intraday_sec=intraday_sec)
    from geoanalytics.futrader.session import in_session  # лёгкий календарь FORTS, без sklearn

    last_day: str | None = None
    last_intraday = 0.0
    consecutive_failures = 0
    try:
        while True:
            ok = True
            # Дневная петля при смене календарного дня (на первом тике — полный прогон).
            try:
                today = datetime.now(UTC).strftime("%Y-%m-%d")
                if today != last_day:
                    _, dok = _safe("futrader_daily", run_futrader_daily)
                    last_day = today  # держим каданс даже при сбое (не ретраим день каждый тик)
                    ok = ok and dok
            except Exception as exc:  # noqa: BLE001 — внешний предохранитель дневной петли
                log.error("futrader_daily_outer_failed", error=str(exc))
                ok = False

            # Интрадей-цикл — ТОЛЬКО внутри сессии FORTS (вкл. рабочие выходные), на своём кадансе.
            if _intraday_due(time.monotonic(), last_intraday, intraday_sec):
                last_intraday = time.monotonic()
                if in_session(datetime.now(), evening=True, allow_weekend=True):
                    _, iok = _safe("futrader_intraday", run_futrader_intraday)
                    ok = ok and iok

            if ok:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures == _WATCHDOG_THRESHOLD:
                    _watchdog_alert(
                        consecutive_failures,
                        title="Futrader нестабилен",
                        message=(f"Торговый цикл падает {consecutive_failures} раз(а) подряд — "
                                 "проверьте логи geo-futrader."),
                        dedup_prefix="futrader_watchdog",
                    )

            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("futrader_loop_stop")
