"""Чистые правила алертов: из срезов состояния → список Alert (без БД).

Три триггера M5.3:
- `price_move_alerts`     — актив изменился на ≥ порога за день;
- `negative_spike_alerts` — всплеск негатива по активу/рынку за окно;
- `new_event_alerts`      — появилось новое значимое событие.

Каждый Alert несёт `dedup_key` — детерминированный ключ срабатывания. У движений
и всплесков он содержит дневной «bucket» (одно уведомление на тикер в день), у
событий — стабильный id (одно уведомление на событие навсегда). Дедупликацию по
этому ключу делает движок через уникальный индекс — здесь только формирование.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from geoanalytics.core.types import EventType

# Категории событий, поднимающие severity до "critical" (всё остальное — "warning").
_CRITICAL_EVENTS = {EventType.SANCTIONS.value, EventType.GEOPOLITICS.value}


@dataclass
class Alert:
    """Готовое к доставке уведомление (результат правила, до записи в БД)."""

    alert_type: str                       # price_move | neg_spike | new_event
    title: str
    message: str
    dedup_key: str
    ticker: str | None = None             # None — рыночный (не по конкретному активу)
    severity: str = "info"                # info | warning | critical
    payload: dict = field(default_factory=dict)
    user_id: int | None = None            # 5c: владелец персонального алерта (None — broadcast)


def price_move_alerts(
    moves: list[dict], threshold_pct: float, bucket: str,
    *, zscore_threshold: float | None = None, min_abs_pct: float = 0.0,
) -> list[Alert]:
    """Алерты по активам с аномальным дневным движением цены.

    `moves`: список ``{ticker, change_pct, last[, sigma_pct]}``. `bucket` — метка дня
    (YYYY-MM-DD) для дедупликации.

    Два режима (G1, Волна 2):
    - z-score (``zscore_threshold`` задан И у движения есть ``sigma_pct`` — дневная
      EWMA-волатильность, %): триггер |change|/σ ≥ порога И |change| ≥ ``min_abs_pct``
      (floor отсекает микродвижения сверхспокойных активов). 5% для голубой фишки и
      третьего эшелона — события разного масштаба; z-score сравнивает движение со
      СВОЕЙ волатильностью актива.
    - фиксированный (как раньше): |change| ≥ ``threshold_pct`` — фолбэк, когда σ нет
      (короткая история) или z-режим выключен.
    Сильное движение (≥ 2× порога своего режима) повышает severity до critical.
    """
    out: list[Alert] = []
    for m in moves:
        chg = m.get("change_pct")
        ticker = m.get("ticker")
        if chg is None or ticker is None:
            continue
        sigma = m.get("sigma_pct")
        z: float | None = None
        if zscore_threshold and sigma:
            z = round(abs(chg) / sigma, 2)
            if z < zscore_threshold or abs(chg) < min_abs_pct:
                continue
            severity = "critical" if z >= 2 * zscore_threshold else "warning"
            why = f"z-score {z:.1f} (σ={sigma:.2f}%)"
        else:
            if abs(chg) < threshold_pct:
                continue
            severity = "critical" if abs(chg) >= 2 * threshold_pct else "warning"
            why = "фикс. порог"
        arrow = "▲" if chg > 0 else "▼"
        last = m.get("last")
        last_str = f", цена {last}" if last is not None else ""
        out.append(Alert(
            alert_type="price_move",
            ticker=ticker,
            severity=severity,
            title=f"{ticker}: {arrow} {chg:+.2f}%",
            message=f"{ticker} изменился на {chg:+.2f}% за день{last_str} ({why}).",
            dedup_key=f"price:{ticker}:{bucket}",
            payload={"change_pct": chg, "last": last, "sigma_pct": sigma, "z": z},
        ))
    return out


def negative_spike_alerts(
    scopes: list[dict], min_count: int, min_ratio: float, bucket: str
) -> list[Alert]:
    """Алерты «всплеск негатива» по рынку, активу, сектору и макро-теме.

    `scopes`: список ``{scope, ticker, negative, total}`` плюс опциональные поля
    ``label`` (человекочитаемый объект, напр. «по сектору «Нефть и газ»»),
    ``kind`` (market/asset/sector/theme) и ``object`` (имя объекта). Для рынка
    scope == "MARKET" и ticker == None; для актива scope == ticker; для сектора/
    темы scope префиксован (``sector:{id}``/``theme:{id}``), чтобы dedup-ключ не
    сталкивался с тикерами. Триггер: ``negative ≥ min_count`` И
    ``negative/total ≥ min_ratio``. `label` (если задан) задаёт объект в тексте —
    он же уходит в Telegram через title.
    """
    out: list[Alert] = []
    for s in scopes:
        neg = s.get("negative", 0)
        total = s.get("total", 0)
        if not total or neg < min_count:
            continue
        ratio = neg / total
        if ratio < min_ratio:
            continue
        scope = s.get("scope", "MARKET")
        ticker = s.get("ticker")
        where = s.get("label") or ("по рынку" if ticker is None else f"по {ticker}")
        out.append(Alert(
            alert_type="neg_spike",
            ticker=ticker,
            severity="warning",
            title=f"Всплеск негатива {where}: {neg}/{total}",
            message=(f"Негативных новостей {where}: {neg} из {total} "
                     f"({ratio * 100:.0f}%) за окно."),
            dedup_key=f"neg:{scope}:{bucket}",
            payload={"negative": neg, "total": total, "ratio": round(ratio, 3),
                     "kind": s.get("kind", "asset" if ticker else "market"),
                     "object": s.get("object", ticker)},
        ))
    return out


def new_event_alerts(
    events: list[dict],
    *,
    require_impact_types: frozenset[str] = frozenset(),
) -> list[Alert]:
    """Алерты по новым значимым событиям.

    `events`: список ``{event_id, event_type, title, impacts: [{ticker, ...}]}``.
    Дедуп по `event_id` (одно уведомление на событие). Тикер — самого значимого
    затронутого актива (impacts отсортированы по убыванию magnitude).

    `require_impact_types` — типы событий, для которых алерт создаётся ТОЛЬКО при
    наличии затронутого актива (непустой `impacts`). Лечит шум: события без
    asset-impact (в основном geopolitics) неоценимы — «затронутые активы: —».
    Пустое множество (по умолчанию) ничего не отсекает.

    Тип `noise` (спорт/происшествия/культура — нерыночный шум) не алертим никогда:
    это явный мусорный класс классификатора событий. Дублирует gate в `_recent_events`,
    но держим в чистом правиле, чтобы оно было корректно независимо от вызывающего.
    """
    out: list[Alert] = []
    for ev in events:
        event_id = ev.get("event_id")
        if event_id is None:
            continue
        etype = ev.get("event_type", EventType.OTHER.value)
        if etype == EventType.NOISE.value:
            continue  # нерыночный шум — не уведомляем
        impacts = ev.get("impacts") or []
        if not impacts and etype in require_impact_types:
            continue  # неоценимое событие без привязки к активу — пропускаем (D1)
        ticker = impacts[0]["ticker"] if impacts else None
        severity = "critical" if etype in _CRITICAL_EVENTS else "warning"
        affected = ", ".join(i["ticker"] for i in impacts[:5]) or "—"
        out.append(Alert(
            alert_type="new_event",
            ticker=ticker,
            severity=severity,
            title=f"[{etype}] {ev.get('title', '')[:120]}",
            message=f"Новое событие ({etype}). Затронутые активы: {affected}.",
            dedup_key=f"event:{event_id}",
            payload={"event_id": event_id, "event_type": etype,
                     "tickers": [i["ticker"] for i in impacts],
                     "url": ev.get("url")},   # ссылка на первоисточник для алерта
        ))
    return out


def combo_alerts(
    price_alerts: list[Alert], neg_alerts: list[Alert], bucket: str
) -> list[Alert]:
    """Комбо-сигнал (D3): падение цены И всплеск негатива по одному активу = сильнее.

    Совпадение двух независимых триггеров на одном активе в один день убедительнее
    каждого по отдельности. Композирует уже готовые выходы `price_move_alerts` и
    `negative_spike_alerts`: берёт активы с НИСХОДЯЩИМ движением цены и пересекает с
    активами, по которым сработал всплеск негатива (scope актива, не рынок/сектор).
    severity — critical; dedup-ключ ``combo:{ticker}:{bucket}`` (одно на актив в день).
    Не отменяет исходные алерты — добавляет усиленный поверх.
    """
    down = {a.ticker: a for a in price_alerts
            if a.ticker and a.payload.get("change_pct", 0) < 0}
    neg = {a.ticker: a for a in neg_alerts if a.ticker}
    out: list[Alert] = []
    for ticker in sorted(set(down) & set(neg)):
        chg = down[ticker].payload.get("change_pct")
        nd = neg[ticker].payload
        n, total = nd.get("negative"), nd.get("total")
        out.append(Alert(
            alert_type="combo",
            ticker=ticker,
            severity="critical",
            title=f"{ticker}: падение {chg:+.2f}% + всплеск негатива",
            message=(f"{ticker}: цена {chg:+.2f}% за день на фоне всплеска негатива "
                     f"({n}/{total} новостей). Совпадение сигналов усиливает вес."),
            dedup_key=f"combo:{ticker}:{bucket}",
            payload={"change_pct": chg, "negative": n, "total": total},
        ))
    return out


def technical_alerts(
    items: list[dict], *, rsi_low: float = 30.0, rsi_high: float = 70.0,
    vol_spike_ratio: float = 3.0,
) -> list[Alert]:
    """Технические алерты по индикаторам актива (D2): RSI, 52w-пробой, кросс SMA, объём.

    `items`: список ``{ticker, bucket, rsi, at_52w_high, at_52w_low, vol_ratio, cross}``
    (значения опциональны; индикаторы считает движок). По каждому активу проверяются:
    - RSI ≥ `rsi_high` (перекупленность) / ≤ `rsi_low` (перепроданность);
    - новый 52-недельный максимум/минимум (`at_52w_high`/`at_52w_low`);
    - golden/death cross SMA50×SMA200 (`cross` ∈ {"golden","death"});
    - всплеск объёма: `vol_ratio` ≥ `vol_spike_ratio`.
    Каждое условие — отдельный Alert с dedup-ключом ``tech:{ticker}:{kind}:{bucket}`` (одно
    уведомление на условие в день). Структурные сигналы (52w, кросс) — severity warning,
    рутинные (RSI, объём) — info.
    """
    out: list[Alert] = []
    for it in items:
        ticker = it.get("ticker")
        if ticker is None:
            continue
        bucket = it.get("bucket", "")

        def _add(kind: str, severity: str, title: str, message: str, payload: dict,
                 _t=ticker, _b=bucket) -> None:
            out.append(Alert(
                alert_type="technical", ticker=_t, severity=severity,
                title=f"{_t}: {title}", message=message,
                dedup_key=f"tech:{_t}:{kind}:{_b}",
                payload={"kind": kind, **payload},
            ))

        rsi_v = it.get("rsi")
        if rsi_v is not None:
            if rsi_v >= rsi_high:
                _add("rsi_overbought", "info", f"RSI {rsi_v:.0f} — перекупленность",
                     f"RSI(14) {rsi_v:.0f} ≥ {rsi_high:.0f}: возможен откат вниз.",
                     {"rsi": rsi_v})
            elif rsi_v <= rsi_low:
                _add("rsi_oversold", "info", f"RSI {rsi_v:.0f} — перепроданность",
                     f"RSI(14) {rsi_v:.0f} ≤ {rsi_low:.0f}: возможен отскок вверх.",
                     {"rsi": rsi_v})

        if it.get("at_52w_high"):
            _add("new_52w_high", "warning", "новый 52-недельный максимум",
                 "Цена обновила годовой максимум.", {})
        if it.get("at_52w_low"):
            _add("new_52w_low", "warning", "новый 52-недельный минимум",
                 "Цена обновила годовой минимум.", {})

        cross = it.get("cross")
        if cross == "golden":
            _add("golden_cross", "warning", "золотой крест (SMA50↑SMA200)",
                 "SMA50 пересекла SMA200 снизу вверх — бычий сигнал.", {})
        elif cross == "death":
            _add("death_cross", "warning", "крест смерти (SMA50↓SMA200)",
                 "SMA50 пересекла SMA200 сверху вниз — медвежий сигнал.", {})

        vr = it.get("vol_ratio")
        if vr is not None and vr >= vol_spike_ratio:
            _add("volume_spike", "info", f"всплеск объёма ×{vr:.1f}",
                 f"Объём за день в {vr:.1f}× выше среднего за 20 дней.", {"vol_ratio": vr})
    return out


# Какие виды календарных событий рыночные (без тикера) и их severity.
_CALENDAR_SEVERITY = {"cbr_rate_meeting": "warning", "dividend_cutoff": "info"}


def calendar_alerts(items: list[dict]) -> list[Alert]:
    """Проактивные алерты по календарю (H2): «завтра заседание ЦБ / отсечка X».

    `items`: список ``{kind, ticker, title, event_date, days_left[, payload]}``
    (срез `upcoming_events`). Дедуп по ``cal:{kind}:{ticker|MKT}:{event_date}`` —
    одно уведомление на событие навсегда (как у new_event): предупреждение за
    день и повтор в день события не нужны. Заседание ЦБ двигает весь рынок —
    severity warning; отсечка — info; неизвестный kind — info.
    """
    out: list[Alert] = []
    for it in items:
        kind = it.get("kind")
        event_date = it.get("event_date")
        if not kind or event_date is None:
            continue
        days_left = it.get("days_left", 0)
        when = {0: "Сегодня", 1: "Завтра"}.get(days_left, f"Через {days_left} дн.")
        ticker = it.get("ticker")
        date_str = event_date.strftime("%d.%m") if hasattr(event_date, "strftime") \
            else str(event_date)
        title_obj = it.get("title") or kind
        prefix = f"{ticker}: " if ticker else ""
        out.append(Alert(
            alert_type="calendar",
            ticker=ticker,
            severity=_CALENDAR_SEVERITY.get(kind, "info"),
            title=f"{prefix}{title_obj} — {date_str}",
            message=f"{when} ({date_str}): {title_obj}.",
            dedup_key=f"cal:{kind}:{ticker or 'MKT'}:{event_date}",
            payload={"kind": kind, "event_date": str(event_date),
                     "days_left": days_left, **(it.get("payload") or {})},
        ))
    return out


def portfolio_alerts(
    report, *, bucket: str, drawdown_pct: float, holding_pnl_pct: float,
    user_id: int | None = None,
) -> list[Alert]:
    """Алерты по портфелю (#6): просадка портфеля и проблемные позиции.

    `report` — `PortfolioReport` (analytics/portfolio.py; берётся по duck-typing, чтобы
    rules остался без зависимости от БД-слоя). Пустой/без цен портфель (`report.error`)
    → пустой список. Триггеры (precision-first, по дню):
    - просадка портфеля: ``max_drawdown_pct ≤ −drawdown_pct`` → один рыночный Alert;
    - проблемная позиция: ``pnl_pct ≤ −holding_pnl_pct`` (только если P&L посчитан от
      avg_price) → Alert на тикер.
    `user_id` (5c) — владелец персонального портфеля: алерт адресуется ему (`Alert.user_id`),
    а dedup получает суффикс `:u{user_id}`, чтобы портфели разных владельцев не схлопывались.
    Портфель владельца (user_id=None) сохраняет прежние ключи (broadcast). VaR-пробой и сдвиг
    факторной экспозиции — follow-up (нужна реализованная дневная доходность портфеля).
    """
    if getattr(report, "error", None):
        return []
    suffix = f":u{user_id}" if user_id is not None else ""
    out: list[Alert] = []

    # max_drawdown_pct — ПОЛОЖИТЕЛЬНАЯ величина просадки (_max_drawdown), порог тоже положителен.
    mdd = report.max_drawdown_pct
    if mdd is not None and mdd >= drawdown_pct:
        out.append(Alert(
            alert_type="portfolio", ticker=None, severity="warning",
            title=f"Портфель: просадка {mdd:.1f}%",
            message=(f"Макс. просадка портфеля {mdd:.1f}% за окно "
                     f"(порог {drawdown_pct:.0f}%)."),
            dedup_key=f"portfolio:drawdown{suffix}:{bucket}",
            payload={"kind": "drawdown", "max_drawdown_pct": mdd},
            user_id=user_id,
        ))

    for p in report.positions:
        pnl = p.pnl_pct
        if pnl is not None and pnl <= -holding_pnl_pct:
            out.append(Alert(
                alert_type="portfolio", ticker=p.ticker, severity="warning",
                title=f"{p.ticker}: позиция в минусе {pnl:.1f}%",
                message=(f"{p.ticker}: P&L позиции {pnl:.1f}% от цены входа "
                         f"(порог −{holding_pnl_pct:.0f}%)."),
                dedup_key=f"portfolio:holding:{p.ticker}{suffix}:{bucket}",
                payload={"kind": "holding", "pnl_pct": pnl},
                user_id=user_id,
            ))
    return out
