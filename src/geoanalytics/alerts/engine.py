"""Движок алертов (M5.3): собирает состояние из БД, гоняет правила, дедуплицирует, шлёт.

Поток: `evaluate` строит срезы (движения цен, негатив по активу/рынку, новые
события) и вызывает чистые правила из `rules`. `evaluate_and_dispatch` пишет новые
алерты в таблицу `alerts` (идемпотентно по `dedup_key` через ON CONFLICT DO NOTHING)
и доставляет только реально новые — повторный прогон не шлёт дубль.

Сбор состояния и запись — в одной транзакции; доставка (сеть) — отдельной, чтобы
не держать транзакцию открытой во время HTTP-вызовов.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from config.settings import Settings, get_settings
from geoanalytics.alerts import channels
from geoanalytics.alerts.rules import (
    Alert,
    calendar_alerts,
    combo_alerts,
    negative_spike_alerts,
    new_event_alerts,
    portfolio_alerts,
    price_move_alerts,
    technical_alerts,
)
from geoanalytics.analytics.attribution import attribute_asset
from geoanalytics.analytics.indicators import ewma_volatility, sma
from geoanalytics.analytics.prices import asset_indicators, close_series
from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import EntityType, EventType, Sentiment
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import (
    AlertMute,
    AlertRecord,
    Article,
    ArticleEntity,
    Asset,
    Event,
    EventImpact,
    MacroTheme,
    Price,
    Sector,
)

log = get_logger("alerts.engine")


@dataclass
class AlertRunResult:
    """Итог прогона движка."""

    evaluated: int = 0   # сколько триггеров сработало (включая уже виденные)
    created: int = 0     # сколько новых записей создано (и доставлено)


# --------------------------------------------------------------------------- #
# Фильтры-условия (MMF по kind, сигнальные каналы по source_ref).
# --------------------------------------------------------------------------- #
def _kind_ok(exclude_kinds):
    """Asset.kind не в исключённых (MMF=fund). Пусто — без фильтра."""
    return Asset.kind.notin_(exclude_kinds) if exclude_kinds else true()


def _source_ok(exclude_sources):
    """Article.source_ref не в исключённых каналах. NULL (RSS/ленты) ВСЕГДА проходит —
    исключаем только перечисленные сигнальные telegram-каналы, не новостные источники."""
    if not exclude_sources:
        return true()
    return or_(Article.source_ref.is_(None), Article.source_ref.notin_(exclude_sources))


# --------------------------------------------------------------------------- #
# Сбор состояния из БД (срезы под чистые правила).
# --------------------------------------------------------------------------- #
def _price_moves(session: Session, exclude_kinds=frozenset()) -> list[dict]:
    """Изменение цены день-к-дню по каждому активу из последних 2 дневных свечей.

    Плюс дневная EWMA-волатильность (`sigma_pct`) по истории закрытий — для
    z-score-режима price_move (G1): движение сравнивается со своей σ актива.
    """
    rn = func.row_number().over(
        partition_by=Price.asset_id, order_by=Price.ts.desc()
    ).label("rn")
    sub = (
        select(Price.asset_id.label("aid"), Price.close.label("close"), rn)
        .where(Price.interval == "1d")
        .subquery()
    )
    rows = session.execute(
        select(Asset.ticker, sub.c.aid, sub.c.close, sub.c.rn)
        .join(Asset, Asset.id == sub.c.aid)
        .where(sub.c.rn <= 2, _kind_ok(exclude_kinds))
        .order_by(sub.c.aid, sub.c.rn)
    )
    by_asset: dict[int, dict] = {}
    for ticker, aid, close, rn_ in rows:
        slot = by_asset.setdefault(aid, {"ticker": ticker})
        slot["last" if rn_ == 1 else "prev"] = float(close)

    moves: list[dict] = []
    for aid, slot in by_asset.items():
        last, prev = slot.get("last"), slot.get("prev")
        if last is not None and prev:
            chg = (last - prev) / prev * 100
            sigma = ewma_volatility(close_series(session, aid, limit=120))
            moves.append({"ticker": slot["ticker"], "change_pct": round(chg, 2),
                          "last": last, "sigma_pct": sigma})
    return moves


def _entity_negative_scopes(
    session: Session, since: datetime, sig_ok, neg: str,
    *, entity_type: str, model, kind: str, label_fmt: str, src_ok=None,
) -> list[dict]:
    """Негатив по сущностям одного типа (сектор/тема) за окно через derived-связи.

    Считает значимые статьи, привязанные к каждой сущности `model` (через
    `ArticleEntity.entity_type == entity_type`, заполняется `geo relink`), и долю
    негатива. dedup-scope префиксован `kind:{id}` — не сталкивается с тикерами.
    """
    story_key = _story_key()
    rows = session.execute(
        select(
            model.id,
            model.name,
            func.count(func.distinct(story_key)).label("total"),
            func.count(func.distinct(story_key))
            .filter(ArticleEntity.sentiment == neg).label("neg"),
        )
        .select_from(ArticleEntity)
        .join(Article, Article.id == ArticleEntity.article_id)
        .join(model, model.id == ArticleEntity.entity_id)
        .where(ArticleEntity.entity_type == entity_type,
               Article.published_at >= since, sig_ok,
               src_ok if src_ok is not None else true())
        .group_by(model.id, model.name)
    )
    return [
        {"scope": f"{kind}:{oid}", "ticker": None, "kind": kind, "object": name,
         "label": label_fmt.format(name=name), "negative": neg_n, "total": tot}
        for oid, name, tot, neg_n in rows
    ]


def _story_key():
    """Ключ уникального сюжета (F6): story_id, а до кластеризации — сама статья.

    Подсчёт негатива по УНИКАЛЬНЫМ сюжетам вместо статей лечит double-counting:
    одно событие, пересказанное N лентами/рерайтами (Б10/Б11), считалось N раз
    и раздувало neg_spike. Отрицательный id не пересекается со story_id."""
    return func.coalesce(Article.story_id, -Article.id)


def _negative_scopes(session: Session, since: datetime, min_sig: float,
                     exclude_kinds=frozenset(), exclude_sources=frozenset()) -> list[dict]:
    """Негатив по рынку, активу, сектору и макро-теме за окно — по значимым новостям.

    Gate `Article.significance >= min_sig` отсекает шум (NULL-значимость старых статей
    тоже отсекается до перелинковки `geo relink`). Сектор/тема считаются по
    derived-связям `ArticleEntity` (актив→сектор/страна, тема) из `relink`/инжеста.
    Счёт — в УНИКАЛЬНЫХ СЮЖЕТАХ (F6), не статьях: см. `_story_key`. Исключаем активы
    видов `exclude_kinds` (MMF) и новости сигнальных каналов `exclude_sources`.
    """
    neg = Sentiment.NEGATIVE.value
    sig_ok = Article.significance >= min_sig
    src_ok = _source_ok(exclude_sources)
    story_key = _story_key()
    scopes: list[dict] = []

    total = session.scalar(
        select(func.count(func.distinct(story_key))).select_from(Article)
        .where(Article.published_at >= since, sig_ok, src_ok)
    ) or 0
    if total:
        neg_total = session.scalar(
            select(func.count(func.distinct(story_key))).select_from(Article)
            .where(Article.published_at >= since, sig_ok, src_ok,
                   Article.sentiment == neg)
        ) or 0
        scopes.append({"scope": "MARKET", "ticker": None, "kind": "market",
                       "object": None, "negative": neg_total, "total": total})

    # Тональность СВЯЗИ (F1: относительно актива), а не статьи; фоновое упоминание
    # (F2: salient=FALSE) в негатив актива не считается (NULL = салиентно, до F2).
    rows = session.execute(
        select(
            Asset.ticker,
            func.count(func.distinct(story_key)).label("total"),
            func.count(func.distinct(story_key))
            .filter(ArticleEntity.sentiment == neg,
                    ArticleEntity.salient.isnot(False)).label("neg"),
        )
        .select_from(ArticleEntity)
        .join(Article, Article.id == ArticleEntity.article_id)
        .join(Asset, Asset.id == ArticleEntity.entity_id)
        .where(ArticleEntity.entity_type == EntityType.ASSET.value,
               Article.published_at >= since, sig_ok, src_ok,
               _kind_ok(exclude_kinds))
        .group_by(Asset.ticker)
    )
    for ticker, tot, neg_n in rows:
        scopes.append({"scope": ticker, "ticker": ticker, "kind": "asset",
                       "object": ticker, "negative": neg_n, "total": tot})

    scopes += _entity_negative_scopes(
        session, since, sig_ok, neg, entity_type=EntityType.SECTOR.value,
        model=Sector, kind="sector", label_fmt="по сектору «{name}»", src_ok=src_ok,
    )
    scopes += _entity_negative_scopes(
        session, since, sig_ok, neg, entity_type=EntityType.MACRO_THEME.value,
        model=MacroTheme, kind="theme", label_fmt="по теме «{name}»", src_ok=src_ok,
    )
    return scopes


def _recent_events(session: Session, since: datetime, min_sig: float,
                   exclude_kinds=frozenset(), exclude_sources=frozenset(),
                   limit: int = 50) -> list[dict]:
    """Новые значимые события за окно с их влиянием на активы (для дедупа по event_id).

    Берём события только по статьям со `significance >= min_sig` (события без привязки
    к статье — например, исторические — тоже отсекаются gate'ом). Исключаем события из
    сигнальных каналов `exclude_sources` и импакты на активы видов `exclude_kinds` (MMF).
    Событийный тип `noise` (спорт/происшествия/культура — нерыночный шум) исключаем прямо
    в запросе: иначе он не только алертит, но и вытесняет значимые события из `limit`.
    """
    rows_ev = session.execute(
        select(Event, Article.url)
        .join(Article, Article.id == Event.article_id)
        .where(Event.occurred_at >= since, Article.significance >= min_sig,
               Event.event_type != EventType.NOISE.value,
               _source_ok(exclude_sources))
        .order_by(Event.occurred_at.desc()).limit(limit)
    ).all()
    out: list[dict] = []
    for ev, url in rows_ev:
        rows = session.execute(
            select(Asset.ticker, EventImpact.direction, EventImpact.magnitude)
            .join(EventImpact, EventImpact.asset_id == Asset.id)
            .where(EventImpact.event_id == ev.id, _kind_ok(exclude_kinds))
            .order_by(EventImpact.magnitude.desc())
        )
        impacts = [{"ticker": t, "direction": d, "magnitude": m} for t, d, m in rows]
        out.append({"event_id": ev.id, "event_type": ev.event_type,
                    "title": ev.title, "impacts": impacts, "url": url})
    return out


def _technical_signals(session: Session, bucket: str,
                       exclude_kinds=frozenset()) -> list[dict]:
    """Снимок технических индикаторов по каждому активу (для `technical_alerts`, D2).

    На актив: полный набор индикаторов сегодня (`asset_indicators` — RSI, дистанция до 52w,
    всплеск объёма, SMA50/200) плюс SMA50/200 вчера (по `closes[:-1]`) для детекта кросса.
    Новый 52w-хай/лой — когда дистанция ровно 0 (последнее закрытие и есть экстремум окна).
    """
    items: list[dict] = []
    for asset in session.scalars(select(Asset).where(_kind_ok(exclude_kinds))):
        ind = asset_indicators(session, asset.id)
        if ind.last is None:
            continue
        cross = None
        closes = close_series(session, asset.id)
        if len(closes) >= 201 and ind.sma50 is not None and ind.sma200 is not None:
            prev50, prev200 = sma(closes[:-1], 50), sma(closes[:-1], 200)
            if prev50 is not None and prev200 is not None:
                if prev50 <= prev200 and ind.sma50 > ind.sma200:
                    cross = "golden"
                elif prev50 >= prev200 and ind.sma50 < ind.sma200:
                    cross = "death"
        items.append({
            "ticker": asset.ticker, "bucket": bucket, "rsi": ind.rsi14,
            "at_52w_high": ind.pct_from_52w_high is not None and ind.pct_from_52w_high >= 0,
            "at_52w_low": ind.pct_from_52w_low is not None and ind.pct_from_52w_low <= 0,
            "vol_ratio": ind.vol_ratio, "cross": cross,
        })
    return items


def _enrich_price_alerts(session: Session, alerts: list[Alert]) -> None:
    """J3: добавляет факторную атрибуцию в сообщение и payload price_move-алертов.

    Обновляет алерты in-place. Сбой атрибуции одного тикера не прерывает остальные.
    """
    for alert in alerts:
        if alert.alert_type != "price_move" or not alert.ticker:
            continue
        try:
            r = attribute_asset(session, alert.ticker)
            if r.error or not r.contributions_pct:
                continue
            parts = [f"{k} {v:+.1f}%" for k, v in r.contributions_pct.items()]
            parts.append(f"идиосинкразия {r.idio_pct:+.1f}%")
            alert.message += f"\nФакторы ({r.r2:.2f} R²): {', '.join(parts)}"
            alert.payload["attribution"] = {
                "contributions_pct": r.contributions_pct,
                "idio_pct": r.idio_pct,
                "r2": r.r2,
            }
        except Exception:  # noqa: BLE001
            pass  # graceful: атрибуция не ломает алерт


def evaluate(session: Session, settings: Settings, *, now: datetime | None = None) -> list[Alert]:
    """Собирает срезы состояния и применяет чистые правила. Сам ничего не пишет."""
    now = now or datetime.now(UTC)
    bucket = now.strftime("%Y-%m-%d")
    since = now - timedelta(hours=settings.alert_window_hours)

    min_sig = settings.alert_min_significance
    exclude_kinds = settings.alert_exclude_kind_set        # MMF (fund) — без алертов
    exclude_sources = settings.alert_exclude_source_set    # сигнальные каналы — без алертов
    alerts: list[Alert] = []
    price = price_move_alerts(
        _price_moves(session, exclude_kinds), settings.alert_price_pct, bucket,
        zscore_threshold=settings.alert_price_zscore or None,
        min_abs_pct=settings.alert_price_min_pct,
    )
    _enrich_price_alerts(session, price)  # J3: факторная атрибуция
    neg = negative_spike_alerts(
        _negative_scopes(session, since, min_sig, exclude_kinds, exclude_sources),
        settings.alert_neg_count, settings.alert_neg_ratio, bucket,
    )
    alerts += price
    alerts += neg
    if settings.alert_combo_enabled:  # D3: падение цены + всплеск негатива = critical
        alerts += combo_alerts(price, neg, bucket)
    alerts += new_event_alerts(
        _recent_events(session, since, min_sig, exclude_kinds, exclude_sources),
        require_impact_types=settings.require_impact_type_set,
    )
    if settings.alert_technical_enabled:
        alerts += technical_alerts(
            _technical_signals(session, bucket, exclude_kinds),
            rsi_low=settings.alert_rsi_low, rsi_high=settings.alert_rsi_high,
            vol_spike_ratio=settings.alert_vol_spike_ratio,
        )
    if settings.alert_calendar_enabled:  # H2: проактивные «завтра заседание ЦБ/отсечка»
        from geoanalytics.context.calendar import upcoming_events

        alerts += calendar_alerts(upcoming_events(
            session, days_ahead=settings.alert_calendar_days_ahead, today=now.date(),
        ))
    if settings.alert_portfolio_enabled:  # #6: просадка портфеля / позиции в минусе
        from geoanalytics.analytics.portfolio import portfolio_report
        from geoanalytics.storage.repositories import PortfolioRepository

        # Владелец (None — broadcast) + каждый личный портфель (адресно владельцу, 5c).
        for uid in [None, *PortfolioRepository(session).owners_with_positions()]:
            rep = portfolio_report(session, user_id=uid)
            if not rep.error:
                alerts += portfolio_alerts(
                    rep, bucket=bucket, user_id=uid,
                    drawdown_pct=settings.alert_portfolio_drawdown_pct,
                    holding_pnl_pct=settings.alert_portfolio_holding_pnl_pct,
                )
    return alerts


# --------------------------------------------------------------------------- #
# Запись (дедуп) и доставка.
# --------------------------------------------------------------------------- #
def _insert_new(session: Session, alert: Alert) -> int | None:
    """Вставляет алерт, если его dedup_key ещё не встречался. id новой записи или None."""
    stmt = (
        pg_insert(AlertRecord)
        .values(
            alert_type=alert.alert_type, ticker=alert.ticker, severity=alert.severity,
            title=alert.title, message=alert.message, dedup_key=alert.dedup_key,
            payload=alert.payload, user_id=alert.user_id,
        )
        .on_conflict_do_nothing(constraint="uq_alert_dedup")
        .returning(AlertRecord.id)
    )
    return session.scalar(stmt)


def _is_muted(alert: Alert, mutes: list[dict], now: datetime) -> bool:
    """Подавлен ли алерт активным mute-правилом (ticker / type / пара тикер+тип).

    Чистая функция: `mutes` — список словарей `{scope_type, scope_value, until}`
    (until=None — бессрочно). Истёкшие (`until <= now`) игнорируются.
    """
    for m in mutes:
        until = m.get("until")
        if until is not None and until <= now:
            continue
        st, sv = m["scope_type"], m["scope_value"]
        if st == "ticker" and alert.ticker is not None and alert.ticker == sv:
            return True
        if st == "type" and alert.alert_type == sv:
            return True
        if st == "ticker_type" and f"{alert.ticker}:{alert.alert_type}" == sv:
            return True
    return False


def _active_mutes(session: Session) -> list[dict]:
    """Все mute-правила как словари (фильтр по `until` — в `_is_muted`); с `user_id` (5b)."""
    rows = session.scalars(select(AlertMute))
    return [{"scope_type": m.scope_type, "scope_value": m.scope_value,
             "until": m.until, "user_id": m.user_id} for m in rows]


def _recipients(session: Session, mutes: list[dict]) -> list[dict]:
    """Получатели алертов (5b): разрешённые пользователи + их личные mute-правила.

    Возвращает ``[{chat_id, mutes}]`` — личные mute каждого (для per-user подавления). Пусто,
    если таблица users ещё не заполнена → вызывающий откатывается на старый allowlist настроек.
    """
    from geoanalytics.storage.repositories import UserRepository

    by_user: dict[int, list[dict]] = {}
    for m in mutes:
        if m["user_id"] is not None:
            by_user.setdefault(m["user_id"], []).append(m)
    return [{"user_id": u.id, "chat_id": u.chat_id, "mutes": by_user.get(u.id, [])}
            for u in UserRepository(session).list_allowed()]


def _delivery_targets(alert: Alert, recipients: list[dict], global_mutes: list[dict],
                      now: datetime) -> tuple[list[str] | None, bool]:
    """Куда доставлять алерт (5b/5c). Чистая: решает получателей с учётом mute и адресности.

    Возвращает ``(targets, muted)``:
    - глобальный mute → ``([], True)`` (подавлен для всех);
    - адресный алерт (`alert.user_id`) → его владельцу, если разрешён и не замьютил лично,
      иначе ``([], True)``;
    - broadcast при наличии получателей → список не замьютивших лично (пусто → подавлен);
    - нет зарегистрированных получателей → ``(None, False)`` — фолбэк на allowlist настроек.
    """
    if _is_muted(alert, global_mutes, now):
        return [], True
    if alert.user_id is not None:
        owner = next((r for r in recipients if r["user_id"] == alert.user_id), None)
        if owner and not _is_muted(alert, owner["mutes"], now):
            return [owner["chat_id"]], False
        return [], True
    if recipients:
        targets = [r["chat_id"] for r in recipients
                   if not _is_muted(alert, r["mutes"], now)]
        return (targets, False) if targets else ([], True)
    return None, False


def evaluate_and_dispatch(dispatch: bool = True) -> AlertRunResult:
    """Полный прогон: вычислить триггеры, записать новые, доставить их.

    Идемпотентно: уже виденные срабатывания (по dedup_key) не создаются повторно и
    не рассылаются. При `dispatch=False` — только фиксация в БД, без уведомлений.
    """
    settings = get_settings()
    pending: list[tuple[int, Alert]] = []
    evaluated = 0
    with session_scope() as session:
        alerts = evaluate(session, settings)
        evaluated = len(alerts)
        for alert in alerts:
            rec_id = _insert_new(session, alert)
            if rec_id is not None:
                pending.append((rec_id, alert))

    muted = 0
    if dispatch and pending:
        now = datetime.now(UTC)
        with session_scope() as session:
            mutes = _active_mutes(session)
            global_mutes = [m for m in mutes if m["user_id"] is None]
            recipients = _recipients(session, mutes)   # 5b: allowed-юзеры + личные mute
            for rec_id, alert in pending:
                targets, is_muted = _delivery_targets(alert, recipients, global_mutes, now)
                if is_muted:
                    chans = ["muted"]
                    muted += 1
                elif targets is None:
                    # Таблица users пуста → прежнее поведение (allowlist настроек).
                    chans = channels.dispatch(alert, settings)
                else:
                    chans = channels.dispatch(alert, settings, chat_ids=targets)
                session.execute(
                    update(AlertRecord).where(AlertRecord.id == rec_id).values(channels=chans)
                )

    log.info("alerts_run", evaluated=evaluated, created=len(pending),
             muted=muted, dispatched=dispatch)
    return AlertRunResult(evaluated=evaluated, created=len(pending))
