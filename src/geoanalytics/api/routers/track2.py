"""HTMX/Jinja router for Track 2 paper trading sandbox and risk monitoring."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from geoanalytics.api import web
from geoanalytics.api.charts import date_labels, sparkline
from geoanalytics.storage.db import session_scope

router = APIRouter()

_TRACK2_ACCOUNT = "demo"


def _attr_rows(by: dict) -> tuple[list[dict], float]:
    """Атрибуция P&L {имя→₽} → строки для бар-чарта (по убыванию) + макс. модуль для ширины."""
    rows = sorted(by.items(), key=lambda kv: kv[1], reverse=True)
    mx = max((abs(v) for _, v in rows), default=0.0)
    return ([{"label": k, "pnl": v, "pct": (abs(v) / mx * 100.0 if mx else 0.0)}
             for k, v in rows], mx)


def _track2_context() -> dict:
    """Панель Трека 2: трек-рекорд бумажного счёта (read-only наблюдение за созреванием).

    Зеркало CLI `track-record`/`risk-status`/`drift`. Всё считают готовые раннеры futrader —
    ничего нового тут не вычисляем. СТРОГО READ-ONLY: дрейф вызываем с `auto_halt=False`, иначе
    раннер ВЗВЁЛ БЫ kill-switch и слал алерт. ORM-объекты разворачиваем в простые dict внутри
    сессии (TTL-кэш переживает её закрытие). Тяжёлое (трек-рекорд+дрейф) — через TTL-кэш.
    """
    def _build() -> dict:
        from geoanalytics.futrader.decisions import SIGNAL_FNS
        from geoanalytics.futrader.monitoring import run_drift_monitor
        from geoanalytics.futrader.risk_limits import RiskLimits
        from geoanalytics.futrader.track import track_record
        from geoanalytics.storage.repositories import (
            FuturesPaperRepository,
            FuturesRiskStateRepository,
        )

        account = web._TRACK2_ACCOUNT
        with session_scope() as session:
            rec = track_record(session, account=account)
            repo = FuturesPaperRepository(session)
            all_positions = repo.positions(account)
            positions = []
            for p in all_positions:
                if p.net_qty == 0:
                    continue
                # duration_bars: сколько 1h-баров с момента последнего входа
                last_entry = repo.last_entry_ts(account, p.asset_code, p.interval, p.source)
                duration_bars = None
                if last_entry:
                    elapsed = (datetime.now(UTC) - last_entry.replace(tzinfo=UTC)
                               if last_entry.tzinfo is None else
                               datetime.now(UTC) - last_entry)
                    duration_bars = max(0, int(elapsed.total_seconds() / 3600))
                # unrealized P&L %
                unreal_pct = None
                if p.avg_price and p.last_price and p.avg_price > 0:
                    unreal_pct = round((p.last_price / p.avg_price - 1) * 100, 2)
                positions.append({
                    "asset_code": p.asset_code, "interval": p.interval, "source": p.source,
                    "net_qty": p.net_qty, "avg_price": p.avg_price, "last_price": p.last_price,
                    "realized_pnl": p.realized_pnl, "duration_bars": duration_bars,
                    "unreal_pct": unreal_pct,
                })
            all_trades = repo.recent_trades(account, limit=50)
            trades = [
                {"ts": t.ts, "asset_code": t.asset_code, "source": t.source, "action": t.action,
                 "signed_qty": t.signed_qty, "price": t.price, "p_win": t.p_win,
                 "realized_pnl": t.realized_pnl, "reason": t.reason,
                 "conviction": t.conviction}
                for t in all_trades[:15]]

            # --- Диагностика выходов (причины + avg P&L) ---
            exit_counts: dict[str, int] = {}
            exit_pnl: dict[str, list[float]] = {}
            for t in all_trades:
                reason = t.reason or "other"
                # группировка: stop_loss / take_profit / time_stop / entry / other
                if reason in ("stop_loss", "take_profit", "time_stop", "entry",
                              "session_flat", "barrier_exit"):
                    key = reason
                else:
                    key = "other"
                exit_counts[key] = exit_counts.get(key, 0) + 1
                if t.realized_pnl is not None:
                    exit_pnl.setdefault(key, []).append(t.realized_pnl)
            exit_diag = []
            for key, cnt in sorted(exit_counts.items(), key=lambda kv: -kv[1]):
                pnls = exit_pnl.get(key, [])
                avg = round(sum(pnls) / len(pnls), 0) if pnls else None
                exit_diag.append({"reason": key, "count": cnt, "avg_pnl": avg,
                                  "pct": round(cnt / max(sum(exit_counts.values()), 1) * 100)})
            time_stop_pct = (exit_counts.get("time_stop", 0) /
                             max(sum(exit_counts.values()), 1) * 100)

            # --- daily_pnl: P&L по дням за 30 дней (для heat-strip) ---
            curve = repo.equity_curve(account, days=30)
            eq_vals = [e.equity for e in curve]
            eq_dates = [e.ts for e in curve]
            # агрегируем equity snapshot → дневной P&L (изменение эквити за день)
            daily_pnl: list[dict] = []
            if curve:
                from collections import defaultdict
                day_eq: dict = defaultdict(list)
                for e in curve:
                    day_key = e.ts.date() if hasattr(e.ts, "date") else str(e.ts)[:10]
                    day_eq[day_key].append(e.equity)
                day_keys = sorted(day_eq.keys())
                for i, dk in enumerate(day_keys):
                    vals = day_eq[dk]
                    if i == 0:
                        pnl_d = 0.0
                    else:
                        prev_vals = day_eq[day_keys[i - 1]]
                        pnl_d = round(vals[-1] - prev_vals[-1], 2)
                    daily_pnl.append({"date": str(dk), "pnl": pnl_d})

            halt = FuturesRiskStateRepository(session).get(account)
            halt_d = ({"halted": halt.halted, "reason": halt.reason,
                       "updated_at": halt.updated_at} if halt else None)
            drift = run_drift_monitor(session, sources=list(SIGNAL_FNS), account=account,
                                      interval="1h", auto_halt=False)

        value_chart = None
        if len(eq_vals) >= 2:
            value_chart = sparkline(eq_vals, width=820, height=200,
                                    labels=date_labels(eq_dates, width=820), dates=eq_dates)
        by_strategy, strat_max = web._attr_rows(rec.by_strategy)
        by_instrument, instr_max = web._attr_rows(rec.by_instrument)
        return {"account": account, "rec": rec, "metrics": rec.metrics, "risk": rec.risk,
                "limits": RiskLimits(), "halt": halt_d, "value_chart": value_chart,
                "positions": positions, "trades": trades, "drift": drift,
                "by_strategy": by_strategy, "strat_max": strat_max,
                "by_instrument": by_instrument, "instr_max": instr_max,
                "daily_pnl": daily_pnl, "exit_diag": exit_diag,
                "time_stop_pct": round(time_stop_pct)}

    return web._cached("track2_report", _build)


@router.get("/ui/track2", response_class=HTMLResponse)
def track2_page(request: Request):
    """Трек 2: песочница фьючерсного бумажного счёта — эквити/метрики/риск/позиции/дрейф."""
    return web.templates.TemplateResponse(request, "track2.html", web._track2_context())


@router.get("/ui/partials/track2", response_class=HTMLResponse)
def track2_partial(request: Request):
    """HTMX-фрагмент панели Трека 2 (автообновление раз в 60с)."""
    return web.templates.TemplateResponse(request, "_track2.html", web._track2_context())
