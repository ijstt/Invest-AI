"""E4 (Волна 1, роудмап v2.0): скоринг фактических исходов алертов.

Алерт-система впервые получает обратную связь: каждый алерт с тикером через
`alert_outcome_horizon_days` торговых дней скорится по фактическим ценам —
двинулся ли актив от закрытия дня срабатывания:

    move = close(base + h) / close(base) − 1,   base = закрытие дня алерта
    abn  = move − движение IMOEX за тот же отрезок
    hit  = |abn| ≥ GEO_ALERT_OUTCOME_MOVE_PCT

Precision по типам алертов (`precision_summary`) — ГЛАВНАЯ метрика системы с
Волны 1: должна монотонно расти от волны к волне. Еженедельный отчёт уходит в
Telegram (дедуп по ISO-неделе через таблицу alerts — не спамит при повторах).
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import get_settings
from geoanalytics.core.logging import get_logger

log = get_logger("alerts.outcomes")

# Типы алертов, у которых есть осмысленный ценовой исход (есть тикер и ожидание
# движения). technical не скорим: RSI/объём — состояния, а не прогнозы движения.
SCORED_TYPES = ("price_move", "neg_spike", "new_event", "combo")


# --------------------------------------------------------------------------- #
# Чистое ядро.
# --------------------------------------------------------------------------- #
def compute_alert_outcome(
    dates: list[date], closes: list[float], alert_date: date, horizon: int,
    index_dates: list[date] | None = None, index_closes: list[float] | None = None,
) -> dict | None:
    """Исход алерта: движение от закрытия дня алерта на `horizon` торговых дней.

    None — горизонт ещё не созрел или нет цен на день алерта. База — последняя
    торговая дата ≤ дня алерта (алерт срабатывает по данным этого дня; выходные
    откатываются к пятнице). Возвращает {base_date, move_pct, abn_move_pct|None}.
    """
    if not dates:
        return None
    base_idx = bisect_right(dates, alert_date) - 1
    if base_idx < 0 or base_idx + horizon >= len(dates):
        return None
    base_close = closes[base_idx]
    if not base_close:
        return None
    move_pct = round((closes[base_idx + horizon] / base_close - 1) * 100, 4)

    abn: float | None = None
    if index_dates and index_closes:
        i_base = bisect_right(index_dates, dates[base_idx]) - 1
        i_end = bisect_right(index_dates, dates[base_idx + horizon]) - 1
        if i_base >= 0 and i_end >= 0 and index_closes[i_base]:
            idx_move = (index_closes[i_end] / index_closes[i_base] - 1) * 100
            abn = round(move_pct - idx_move, 4)
    return {"base_date": dates[base_idx], "move_pct": move_pct, "abn_move_pct": abn}


def is_hit(move_pct: float, abn_move_pct: float | None, threshold_pct: float) -> bool:
    """Сработал ли алерт по факту: |market-adjusted движение| ≥ порога.

    Без индекса — по сырому движению (честная деградация, не ноль данных).
    """
    value = abn_move_pct if abn_move_pct is not None else move_pct
    return abs(value) >= threshold_pct


# --------------------------------------------------------------------------- #
# DB-раннер.
# --------------------------------------------------------------------------- #
@dataclass
class ScoreResult:
    """Итог прогона скоринга алертов."""

    scored: int = 0
    hits: int = 0
    pending: int = 0   # горизонт ещё не созрел
    skipped: int = 0   # нет цен по тикеру
    errors: int = 0


def _series(session: Session, asset_id: int) -> tuple[list[date], list[float]]:
    from geoanalytics.storage.models import Price

    rows = session.execute(
        select(Price.ts, Price.close)
        .where(Price.asset_id == asset_id, Price.interval == "1d")
        .order_by(Price.ts)
    ).all()
    return [ts.date() for ts, _ in rows], [float(c) for _, c in rows]


def score_alert_outcomes(limit: int | None = None) -> ScoreResult:
    """Скорит все алерты поддерживаемых типов без записанного исхода.

    Идемпотентно: уже скоренные отфильтрованы анти-джойном + UNIQUE(alert_id).
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import AlertOutcome, AlertRecord, Asset
    from geoanalytics.storage.seed import BENCHMARK_TICKER

    settings = get_settings()
    horizon = settings.alert_outcome_horizon_days
    threshold = settings.alert_outcome_move_pct
    result = ScoreResult()

    with session_scope() as session:
        asset_ids = {t: aid for aid, t in session.execute(select(Asset.id, Asset.ticker))}
        index_dates, index_closes = ([], [])
        if BENCHMARK_TICKER in asset_ids:
            index_dates, index_closes = _series(session, asset_ids[BENCHMARK_TICKER])

        stmt = (
            select(AlertRecord.id, AlertRecord.ticker, AlertRecord.created_at)
            .outerjoin(AlertOutcome, AlertOutcome.alert_id == AlertRecord.id)
            .where(
                AlertRecord.ticker.is_not(None),
                AlertRecord.alert_type.in_(SCORED_TYPES),
                AlertOutcome.id.is_(None),
            )
            .order_by(AlertRecord.created_at)
        )
        if limit:
            stmt = stmt.limit(limit)
        rows = session.execute(stmt).all()

        series_cache: dict[int, tuple[list[date], list[float]]] = {}
        for alert_id, ticker, created_at in rows:
            try:
                asset_id = asset_ids.get(ticker)
                if asset_id is None:
                    result.skipped += 1
                    continue
                if asset_id not in series_cache:
                    series_cache[asset_id] = _series(session, asset_id)
                dates, closes = series_cache[asset_id]
                outcome = compute_alert_outcome(
                    dates, closes, created_at.date(), horizon,
                    index_dates, index_closes,
                )
                if outcome is None:
                    result.pending += 1
                    continue
                hit = is_hit(outcome["move_pct"], outcome["abn_move_pct"], threshold)
                ins = (
                    pg_insert(AlertOutcome)
                    .values(alert_id=alert_id, ticker=ticker,
                            base_date=outcome["base_date"], horizon_days=horizon,
                            move_pct=outcome["move_pct"],
                            abn_move_pct=outcome["abn_move_pct"], hit=hit)
                    .on_conflict_do_nothing(index_elements=["alert_id"])
                )
                if session.execute(ins).rowcount:
                    result.scored += 1
                    result.hits += int(hit)
            except Exception as exc:  # noqa: BLE001 — один алерт не валит прогон
                result.errors += 1
                log.error("alert_outcome_failed", alert_id=alert_id, error=str(exc))
    log.info("alert_outcomes_scored", scored=result.scored, hits=result.hits,
             pending=result.pending, skipped=result.skipped, errors=result.errors)
    return result


# --------------------------------------------------------------------------- #
# Precision-метрика и еженедельный отчёт.
# --------------------------------------------------------------------------- #
def precision_summary(days: int = 30) -> list[dict]:
    """Precision по типам алертов за trailing-окно: [{alert_type, n, hits, precision}].

    Считается по скоренным исходам (alert_outcomes ⋈ alerts). Это и есть глобальная
    метрика системы Волны 1.
    """
    from sqlalchemy import func

    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import AlertOutcome, AlertRecord

    since = datetime.now(UTC) - timedelta(days=days)
    with session_scope() as session:
        rows = session.execute(
            select(
                AlertRecord.alert_type,
                func.count().label("n"),
                func.count().filter(AlertOutcome.hit.is_(True)).label("hits"),
            )
            .join(AlertOutcome, AlertOutcome.alert_id == AlertRecord.id)
            .where(AlertRecord.created_at >= since)
            .group_by(AlertRecord.alert_type)
            .order_by(AlertRecord.alert_type)
        ).all()
    return [
        {"alert_type": t, "n": n, "hits": h,
         "precision": round(h / n, 3) if n else None}
        for t, n, h in rows
    ]


def weekly_report_text(summary: list[dict], days: int = 30) -> str:
    """Текст еженедельного отчёта precision для Telegram."""
    if not summary:
        return (f"📊 Precision алертов за {days} дн.: скоренных исходов пока нет "
                f"(копятся с Волны 1).")
    lines = [f"📊 Precision алертов за {days} дн. (|движение − IMOEX| ≥ "
             f"{get_settings().alert_outcome_move_pct:.1f}% за "
             f"{get_settings().alert_outcome_horizon_days} торг. дн.):"]
    for s in summary:
        lines.append(
            f"• {s['alert_type']}: {s['hits']}/{s['n']} = {s['precision']:.0%}"
        )
    total_n = sum(s["n"] for s in summary)
    total_h = sum(s["hits"] for s in summary)
    if total_n:
        lines.append(f"Итого: {total_h}/{total_n} = {total_h / total_n:.0%}")
    return "\n".join(lines)


def send_weekly_report(days: int = 30) -> bool:
    """Шлёт еженедельный отчёт precision (дедуп по ISO-неделе через таблицу alerts).

    True — отчёт новый и отправлен; False — на этой неделе уже был (или нет каналов).
    """
    from geoanalytics.alerts import channels
    from geoanalytics.alerts.engine import _insert_new
    from geoanalytics.alerts.rules import Alert
    from geoanalytics.storage.db import session_scope

    settings = get_settings()
    summary = precision_summary(days=days)
    iso = datetime.now(UTC).isocalendar()
    alert = Alert(
        alert_type="report",
        severity="info",
        title="Еженедельный отчёт: precision алертов",
        message=weekly_report_text(summary, days=days),
        dedup_key=f"report:alert_precision:{iso.year}-W{iso.week:02d}",
        payload={"summary": summary, "days": days},
    )
    with session_scope() as session:
        rec_id = _insert_new(session, alert)
    if rec_id is None:
        return False
    channels.dispatch(alert, settings)
    log.info("alert_precision_report_sent", week=f"{iso.year}-W{iso.week:02d}",
             types=len(summary))
    return True
