"""Периодический сбор данных.

Источники опрашиваются с разной частотой (Фаза D) — базовый тик цикла короткий, а каждый
коннектор тянется только когда истёк его интервал по типу:
  - рынок (МосБиржа)            — каждый тик (интрадей);
  - новости (Интерфакс/РБК/…)   — ~раз в 15 минут;
  - макро (ЦБ РФ/ЕЦБ/ФРС/FORTS) — раз в день.

Это снимает лишнюю нагрузку с источников (троттлинг iss.moex.com) и не дёргает дневные
макро-ряды каждые 5 минут. Команда-точка входа — `geo run-scheduler`.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from config.settings import get_settings
from geoanalytics.alerts.engine import evaluate_and_dispatch
from geoanalytics.connectors.base import BaseConnector
from geoanalytics.connectors.registry import all_connectors
from geoanalytics.connectors.service import ingest_source
from geoanalytics.context.events import build_events
from geoanalytics.context.stories import assign_stories
from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import SourceKind
from geoanalytics.health import report as health_report
from geoanalytics.processing import process_pending
from geoanalytics.storage.retention import prune

log = get_logger("scheduler")


def due_sources(connectors: list[BaseConnector], last_run: dict[str, float], now: float,
                intervals: dict[SourceKind, int]) -> list[str]:
    """Имена коннекторов, которым пора собираться: прошло ≥ интервала их типа с прошлого раза.

    Чистая (без БД/сети) — легко тестируется. Источник, которого ещё не было в `last_run`,
    считается просроченным → на первом тике собираются все (полный прогон при старте)."""
    due: list[str] = []
    for c in connectors:
        interval = intervals.get(c.kind)
        if interval is None:
            continue
        if now - last_run.get(c.name, float("-inf")) >= interval:
            due.append(c.name)
    return due


# Общие примитивы цикла (`_safe`/`_intraday_due`/`_watchdog_alert`/`_WATCHDOG_THRESHOLD`) вынесены
# в `runtime`, чтобы их разделял торговый раннер (futrader_runner) без импорта тяжёлого scheduler.
# Ре-экспорт сохраняет публичные пути импорта (в т.ч. для tests/test_scheduler.py).
from geoanalytics.orchestration.runtime import (  # noqa: E402
    _WATCHDOG_THRESHOLD,
    _intraday_due,
    _safe,
    _watchdog_alert,
)

__all__ = ["due_sources", "_intraday_due", "_safe", "_watchdog_alert", "_run_cycle", "run"]


def _run_cycle(last_run: dict[str, float], intervals: dict[SourceKind, int]) -> bool:
    """Один цикл сбора с поэтапной изоляцией сбоев (Б15). Возвращает cycle_ok.

    Каждый этап (ingest/process/stories/events/alerts) в своём `_safe` — частичный
    прогресс сохраняется, а cycle_ok=False, если хоть один этап упал.
    """
    connectors = all_connectors()
    now = time.monotonic()
    names = due_sources(connectors, last_run, now, intervals)

    results = []
    cycle_ok = True
    for name in names:
        r, ok = _safe("ingest", ingest_source, name)
        last_run[name] = now  # держим каденс даже при сбое (ретрай по своему интервалу)
        cycle_ok = cycle_ok and ok
        if r is not None:
            results.append(r)

    proc, ok = _safe("process", process_pending)
    cycle_ok = cycle_ok and ok
    # Сюжеты (F6) ДО алертов: neg_spike считает уникальные сюжеты.
    _, ok = _safe("stories", assign_stories)
    cycle_ok = cycle_ok and ok
    events, ok = _safe("events", build_events)
    cycle_ok = cycle_ok and ok
    # Алерты — после обновления данных/событий (виденные по dedup_key молча пропускаются).
    alerts, ok = _safe("alerts", evaluate_and_dispatch)
    cycle_ok = cycle_ok and ok

    log.info("scheduler_cycle", ingested=names,
             stored=sum(r.stored for r in results),
             articles=proc.articles if proc is not None else 0,
             events=events if events is not None else 0,
             alerts=alerts.created if alerts is not None else 0)
    # Heartbeat: метка успешного тика — health/оператор видит свежесть последнего цикла.
    log.info("scheduler_heartbeat", at=datetime.now(UTC).isoformat(), ok=cycle_ok)
    return cycle_ok


def run(interval: int | None = None) -> None:
    """Бесконечный цикл сбора: каждый тик собирает только просроченные источники.

    `interval` (сек) — базовый тик, по умолчанию `GEO_SCHEDULER_INTERVAL_SEC` (300). Частота
    каждого источника задаётся `GEO_{MARKET,NEWS,MACRO}_INTERVAL_SEC`. Ретеншн (`prune`) —
    раз в сутки (при смене календарного дня).

    Б15: ни один сбой (этапа, цикла, ежедневной джобы) не роняет демон — всё логируется и
    цикл продолжается; при `_WATCHDOG_THRESHOLD` подряд-сбойных циклах уходит Telegram-алерт."""
    settings = get_settings()
    if interval is None:
        interval = settings.scheduler_interval_sec
    intervals = {
        SourceKind.MARKET: settings.market_interval_sec,
        SourceKind.NEWS: settings.news_interval_sec,
        SourceKind.MACRO: settings.macro_interval_sec,
    }
    log.info("scheduler_start", interval=interval,
             intervals={k.value: v for k, v in intervals.items()})
    # I4 (Волна 1): громкая проверка фолбэков при старте — если модель значимости/
    # тональности не поднялась, узнаём сразу (Telegram), а не по сдвигу алертов.
    health_report(send_alerts=True)
    last_run: dict[str, float] = {}  # имя источника → time.monotonic() последнего сбора
    last_prune_day: str | None = None
    consecutive_failures = 0
    try:
        while True:
            try:
                cycle_ok = _run_cycle(last_run, intervals)
            except Exception as exc:  # noqa: BLE001 — Б15: внешний предохранитель демона
                log.error("scheduler_cycle_failed", error=str(exc))
                cycle_ok = False

            if cycle_ok:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                # Алерт один раз при пересечении порога (не каждый цикл); восстановление
                # обнуляет счётчик, так что новый сбой снова уведомит.
                if consecutive_failures == _WATCHDOG_THRESHOLD:
                    _watchdog_alert(consecutive_failures)

            # Ретеншн + ежедневные джобы при смене календарного дня (тоже под предохранителем).
            try:
                today = datetime.now(UTC).strftime("%Y-%m-%d")
                if today != last_prune_day:
                    pruned, _ = _safe("prune", prune)
                    last_prune_day = today
                    if pruned is not None:
                        log.info("scheduler_prune", articles=pruned.articles,
                                 raw=pruned.raw_documents)
                    _daily_jobs()
            except Exception as exc:  # noqa: BLE001 — ежедневное не валит цикл сбора
                log.error("scheduler_daily_failed", error=str(exc))

            # Трек 2 (futrader) — интрадей-цикл и дневная петля — вынесены в отдельный процесс
            # `geo run-futrader` (geoanalytics.orchestration.futrader_runner): чисто numeric, свой
            # потолок памяти, Raspberry-Pi-ready. Здесь их больше нет — пик памяти scheduler не
            # разгоняется futrader-петлёй (см. cozy-toasting-bunny.md, Трек A).

            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("scheduler_stop")


def _daily_jobs() -> None:
    """Ежедневные джобы Волны 1: рыночная разметка, скоринг алертов, health.

    Каждый шаг в своём try — сбой одного не валит ни остальные, ни цикл сбора
    (scheduler монолитен — Б15, поэтому страхуемся локально).
    """
    try:
        from geoanalytics.analytics.outcomes import label_news_outcomes

        r = label_news_outcomes()
        log.info("scheduler_outcomes", labeled=r.labeled, pending=r.pending)
    except Exception as exc:  # noqa: BLE001
        log.error("scheduler_outcomes_failed", error=str(exc))
    try:
        from geoanalytics.alerts.outcomes import score_alert_outcomes, send_weekly_report

        r = score_alert_outcomes()
        log.info("scheduler_alert_outcomes", scored=r.scored, hits=r.hits)
        # Отчёт precision — по понедельникам; дедуп по ISO-неделе внутри.
        if datetime.now(UTC).isoweekday() == 1:
            send_weekly_report()
    except Exception as exc:  # noqa: BLE001
        log.error("scheduler_alert_outcomes_failed", error=str(exc))
    try:
        # Непрерывная оценка значимости против рынка (I2) — еженедельно (понедельник); пишет
        # метрику в eval_runs и алертит при дрейфе качества относительно трейлинг-базы.
        if datetime.now(UTC).isoweekday() == 1:
            from geoanalytics.analytics.continuous_eval import (
                run_continuous_eval,
                run_stance_eval,
            )
            from geoanalytics.storage.db import session_scope

            with session_scope() as session:
                s = run_continuous_eval(session)
            log.info("scheduler_continuous_eval", recorded=s.recorded,
                     precision=None if s.agreement.precision is None
                     else round(s.agreement.precision, 3), drifted=s.drift.drifted)
            # C1: калибровка направленной стойки против реализованного движения рынка (дозрело).
            with session_scope() as session:
                sd = run_stance_eval(session)
            log.info("scheduler_stance_eval", recorded=sd.recorded,
                     precision=None if sd.agreement.precision is None
                     else round(sd.agreement.precision, 3), drifted=sd.drift.drifted)
    except Exception as exc:  # noqa: BLE001
        log.error("scheduler_continuous_eval_failed", error=str(exc))
    try:
        # Курсы ЦБ (USD/EUR/CNY): однодневный коннектор process-цикла (XML_daily — только сегодня)
        # оставляет ПОСТОЯННУЮ дыру после простоя. Диапазонный backfill_fx идемпотентно дозаполняет
        # окно — это фундамент для режимов/атрибуции/корреляций.
        from geoanalytics.analytics.history import backfill_fx

        res = backfill_fx(days=7)
        log.info("scheduler_fx_backfill", currencies=len(res),
                 points=sum(r.points for r in res))
    except Exception as exc:  # noqa: BLE001
        log.error("scheduler_fx_backfill_failed", error=str(exc))
    try:
        # B1: дневной индекс настроения (рынок/сектор/актив) — материализуем агрегат сентимента
        # во времени (тренд/breadth/дивергенция). backfill (а не record_day) самозалечивает
        # пропуски окна после простоя; идемпотентно, переносит EWMA по возрастанию дней.
        from geoanalytics.analytics.market_sentiment import backfill
        from geoanalytics.storage.db import session_scope

        with session_scope() as session:
            rows = backfill(session, days=7)
        log.info("scheduler_market_sentiment", rows=rows)
    except Exception as exc:  # noqa: BLE001
        log.error("scheduler_market_sentiment_failed", error=str(exc))
    try:
        # L3: дневной срез кросс-секционных факторов (value/quality/growth/композит) по вселенной
        # акций — копим тренд факторных экспозиций во времени.
        from geoanalytics.analytics.factor_model import backfill_scores
        from geoanalytics.storage.db import session_scope

        # backfill_scores самозалечивает пропущенные дни окна (квазистатичный фактор), а не
        # только сегодня — иначе после простоя в ряду факторов остаётся дыра.
        with session_scope() as session:
            rows = backfill_scores(session, days=7)
        log.info("scheduler_factor_scores", rows=rows)
    except Exception as exc:  # noqa: BLE001
        log.error("scheduler_factor_scores_failed", error=str(exc))
    try:
        # L5: дневной снимок режима рынка (HMM по vol IMOEX/USD) — копим историю режимов.
        from geoanalytics.analytics.regimes import record_regimes
        from geoanalytics.storage.db import session_scope

        with session_scope() as session:
            days = record_regimes(session)
        log.info("scheduler_market_regimes", days=days)
    except Exception as exc:  # noqa: BLE001
        log.error("scheduler_market_regimes_failed", error=str(exc))
    if datetime.now(UTC).isoweekday() == 1:
        try:
            # L5: еженедельное накопление средне-долгосрочного нарратива (AssetContext) по всем
            # акциям. use_llm=False — шаблонный нарратив без Ollama (батч не конкурирует за GPU).
            from sqlalchemy import select

            from geoanalytics.context.asset_context import build_context
            from geoanalytics.storage.db import session_scope
            from geoanalytics.storage.models import Asset

            with session_scope() as session:
                tickers = list(session.scalars(
                    select(Asset.ticker).where(Asset.kind == "share").order_by(Asset.ticker)))
            built = 0
            for tk in tickers:
                try:
                    if build_context(tk, use_llm=False) is not None:
                        built += 1
                except Exception as exc:  # noqa: BLE001 — один тикер не должен ронять батч
                    log.warning("asset_context_accumulate_skip", ticker=tk, error=str(exc))
            log.info("scheduler_asset_context", built=built, total=len(tickers))
        except Exception as exc:  # noqa: BLE001
            log.error("scheduler_asset_context_failed", error=str(exc))
    try:
        from geoanalytics.context.calendar import sync_calendar

        r = sync_calendar()
        log.info("scheduler_calendar", cbr=r.cbr, dividends=r.dividends,
                 smartlab=r.smartlab, errors=r.errors)
    except Exception as exc:  # noqa: BLE001
        log.error("scheduler_calendar_failed", error=str(exc))
    try:
        # Фонды денежного рынка (kind=fund) и фьючерсы FORTS (kind=future, C2) не входят в
        # live-срез TQBR — обновляем их EOD-историю ежедневным бэкфиллом (идемпотентно), иначе
        # их цена застынет. Фьючерсы с пустым ответом ISS дают 0 свечей (graceful).
        from sqlalchemy import select

        from geoanalytics.analytics.history import backfill_asset
        from geoanalytics.storage.db import session_scope
        from geoanalytics.storage.models import Asset

        with session_scope() as session:
            offline = [t for (t,) in session.execute(
                select(Asset.ticker).where(Asset.kind.in_(("fund", "future"))))]
        new_candles = sum(backfill_asset(t).candles for t in offline)
        log.info("scheduler_offline_backfill", assets=len(offline), new_candles=new_candles)
    except Exception as exc:  # noqa: BLE001
        log.error("scheduler_offline_backfill_failed", error=str(exc))
    try:
        # Полный sweep reconcile_impacts: обычные циклы строят/обновляют EventImpact, но НЕ прунят
        # ghost-связи (потерявшие salience при переклассификации) — дрейф копился до ручного relink.
        # Ежедневный полный проход держит event_impacts честными (закрывает model-data-errors #1).
        from geoanalytics.context.events import reconcile_impacts
        from geoanalytics.storage.db import session_scope

        with session_scope() as session:
            stats = reconcile_impacts(session)
        log.info("scheduler_reconcile_impacts", pruned=stats["pruned"], rebuilt=stats["rebuilt"])
    except Exception as exc:  # noqa: BLE001
        log.error("scheduler_reconcile_impacts_failed", error=str(exc))
    if datetime.now(UTC).isoweekday() == 1:
        try:
            # H5: еженедельный скрейп фундаменталки (smartlab MSFO) по всем акциям — иначе
            # asset_fundamentals замерзает (PDF-ингест ручной). Сбой тикера изолирован.
            from sqlalchemy import select

            from geoanalytics.analytics.fundamentals import scrape_fundamentals
            from geoanalytics.storage.db import session_scope
            from geoanalytics.storage.models import Asset

            scraped = 0
            with session_scope() as session:
                tickers = list(session.scalars(
                    select(Asset.ticker).where(Asset.kind == "share").order_by(Asset.ticker)))
                for tk in tickers:
                    try:
                        scraped += scrape_fundamentals(session, tk).stored
                    except Exception as exc:  # noqa: BLE001 — один тикер не валит батч
                        log.warning("fundamentals_scrape_skip", ticker=tk, error=str(exc))
            log.info("scheduler_fundamentals_scrape", tickers=len(tickers), stored=scraped)
        except Exception as exc:  # noqa: BLE001
            log.error("scheduler_fundamentals_scrape_failed", error=str(exc))
    try:
        # Снимок стоимости каждого портфеля по свежим ценам (после бэкфилла фондов) — копит
        # фактическую историю стоимости/P&L вместо реконструкции по текущему составу. ВАЖНО: дешёвый
        # и важный шаг идёт ДО тяжёлой futrader-петли — чтобы прерывание/медленный loop не лишали
        # дашборд свежего снимка (после простоя ловили это: снимков не было за дни даунтайма).
        from geoanalytics.analytics.portfolio import snapshot_portfolios
        from geoanalytics.storage.db import session_scope

        with session_scope() as session:
            n = snapshot_portfolios(session)
        log.info("scheduler_portfolio_snapshots", portfolios=n)
    except Exception as exc:  # noqa: BLE001
        log.error("scheduler_portfolio_snapshots_failed", error=str(exc))
    # Трек 2 (futrader) ДНЕВНАЯ петля (accumulate → train/eval → paper, еженедельные PBO/drift)
    # вынесена в отдельный процесс `geo run-futrader` (futrader_runner.run_futrader_daily): это был
    # ~8G-пик памяти scheduler. Здесь её больше нет (см. cozy-toasting-bunny.md, Трек A). scheduler
    # по-прежнему готовит данные, которые трейдер читает (market_regimes/market_sentiment выше).
    try:
        from geoanalytics.query.digest import send_daily_digest

        sent = send_daily_digest()
        if sent:
            log.info("scheduler_digest_sent")
    except Exception as exc:  # noqa: BLE001
        log.error("scheduler_digest_failed", error=str(exc))
    # Nightly health: деградация фолбэков заметна не позже чем через сутки (I4).
    health_report(send_alerts=True)
