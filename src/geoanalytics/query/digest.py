"""J2: ежедневный дайджест рынка.

Агрегирует ключевые сигналы в один компактный отчёт:
макро → движения цен → новостной фон по активам → ближайшие события → топ-новости.
Отправляется раз в день в Telegram (дедуп по дате).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from geoanalytics.analytics.pressure import news_pressure
from geoanalytics.analytics.sentiment_trend import latest_momentum
from geoanalytics.context.calendar import upcoming_events
from geoanalytics.query.news_summary import build_snapshot
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import Asset


def build_digest_text(hours: int = 24) -> str:
    """Строит текст ежедневного дайджеста рынка."""
    snap = build_snapshot(top_n=3, headline_n=6, hours=hours, use_llm=False)

    with session_scope() as session:
        assets = list(session.scalars(
            select(Asset).where(Asset.kind == "share").order_by(Asset.ticker)
        ))
        asset_rows: list[tuple[str, float, float | None]] = []
        for asset in assets:
            p = news_pressure(session, asset.id, window=7)
            m_val = latest_momentum(session, asset.id, span=14)
            asset_rows.append((asset.ticker, p, m_val))

        events = upcoming_events(session, days_ahead=5)

    today = datetime.now(UTC).strftime("%d.%m.%Y")
    lines: list[str] = [f"Дайджест рынка {today}", "-" * 28]

    # Макро
    macro_parts: list[str] = []
    if snap.key_rate is not None:
        macro_parts.append(f"ставка {snap.key_rate}%")
    for cur, val in snap.fx.items():
        macro_parts.append(f"{cur} {val:.2f}₽")
    if macro_parts:
        lines.append("Макро: " + ", ".join(macro_parts))

    # Движения рынка
    movers: list[str] = []
    for m in (snap.top_gainers or [])[:3]:
        movers.append(f"{m['ticker']} {m['change_pct']:+.1f}%▲")
    for m in (snap.top_losers or [])[:3]:
        movers.append(f"{m['ticker']} {m['change_pct']:+.1f}%▼")
    if movers:
        lines.append("Движения: " + "  ".join(movers))

    # Новостной фон по активам
    active_rows = [(t, p, m) for t, p, m in asset_rows if p > 0.01 or m is not None]
    if active_rows:
        lines.append("\nНовостной фон (давл.7д / EMA-14 сент.):")
        for ticker, p, m_val in active_rows[:12]:
            trend = ""
            if m_val is not None:
                trend = " ▲" if m_val > 0.05 else (" ▼" if m_val < -0.05 else " –")
            m_str = f"{m_val:+.3f}" if m_val is not None else " n/a"
            lines.append(f"  {ticker:<6}  {p:.3f} / {m_str}{trend}")

    # Ближайшие события (calendar)
    if events:
        lines.append("\nБлижайшие события:")
        for ev in events[:6]:
            dt = ev["event_date"].strftime("%d.%m") if ev.get("event_date") else "?"
            ticker_str = f" ({ev['ticker']})" if ev.get("ticker") else ""
            lines.append(f"  {dt}{ticker_str}: {ev['title']}")

    # Топ-новости
    if snap.headlines:
        lines.append("\nГлавное за 24ч:")
        for h in snap.headlines[:5]:
            sig = h.get("significance") or 0.0
            lines.append(f"  [{sig:.2f}] {h['title']}")

    return "\n".join(lines)


def _portfolio_digest_line(rep) -> str:
    """Компактная строка портфеля для персонального дайджеста (5c)."""
    if getattr(rep, "error", None):
        return "💼 Портфель: пуст"
    total = f"{rep.total_value_rub:,.0f}".replace(",", " ")
    parts = [f"💼 Портфель: {total} ₽"]
    if rep.var95_1d_pct is not None:
        parts.append(f"VaR95 {rep.var95_1d_pct:g}%")
    if rep.max_drawdown_pct is not None:
        parts.append(f"просадка {rep.max_drawdown_pct:g}%")
    losers = [p.ticker for p in rep.positions if (p.pnl_pct or 0) < 0]
    if losers:
        parts.append("в минусе: " + ", ".join(losers[:5]))
    return " · ".join(parts)


def send_daily_digest(hours: int = 24) -> bool:
    """Отправляет дайджест в Telegram (дедуп по дате — раз в день).

    5c: ПЕРСОНАЛЬНО каждому allowed-пользователю — общий рыночный блок + сводка ЕГО портфеля
    (admin → портфель владельца, обычный → свой), адресной доставкой. Если таблица users пуста
    — один broadcast рыночного дайджеста (прежнее поведение). True, если хоть один отправлен.
    """
    from config.settings import get_settings
    from geoanalytics.alerts import channels
    from geoanalytics.alerts.engine import _insert_new
    from geoanalytics.alerts.rules import Alert
    from geoanalytics.analytics.portfolio import live_portfolio_report
    from geoanalytics.storage.repositories import UserRepository

    settings = get_settings()
    today_str = datetime.now(UTC).strftime("%Y-%m-%d")
    market = build_digest_text(hours=hours)

    with session_scope() as session:
        users = [(u.id, u.chat_id, u.role)
                 for u in UserRepository(session).list_allowed()]

    if not users:  # фолбэк: рыночный broadcast (нет зарегистрированных пользователей)
        alert = Alert(alert_type="digest", severity="info",
                      title=f"Дайджест рынка {today_str}", message=market,
                      dedup_key=f"digest:market:{today_str}", payload={"date": today_str})
        with session_scope() as session:
            rec_id = _insert_new(session, alert)
        if rec_id is None:
            return False
        channels.dispatch(alert, settings)
        return True

    sent = 0
    for uid, chat_id, role in users:
        scope = None if role == "admin" else uid
        with session_scope() as session:
            rep = live_portfolio_report(session, user_id=scope)
        alert = Alert(
            alert_type="digest", severity="info",
            title=f"Дайджест {today_str}",
            message=market + "\n\n" + _portfolio_digest_line(rep),
            dedup_key=f"digest:u{uid}:{today_str}",
            payload={"date": today_str}, user_id=uid,
        )
        with session_scope() as session:
            rec_id = _insert_new(session, alert)
        if rec_id is None:
            continue
        if channels.dispatch(alert, settings, chat_ids=[chat_id]) != ["console"]:
            sent += 1
    return sent > 0
