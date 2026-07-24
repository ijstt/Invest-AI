"""Трек 2 / Фаза D (T2.5): бумажный счёт + гейт качества + петля самообучения.

Замыкает концепцию «учится на своих исходах»:
  1. **Гейт качества**: стратегия торгуется ТОЛЬКО если её пулинг-чемпион из реестра (Фаза B)
     прошёл планку (положительный lift И положительный OOS Sharpe И минимум сделок). Нет чемпиона
     или слаб — инструмент не торгуется (human/gate в петле, без авто-риска на шуме).
  2. **Исполнение**: на свежих барах чемпион-модель решает вход/выход; размер — Фаза C
     (vol-targeting × дробный Келли); circuit-breaker по просадке счёта. Сделки — на БУМАЖНЫЙ счёт
     (журнал + позиция в БД), реальных ордеров нет.
  3. **Петля**: периодически accumulate→train→evaluate промоутит лучшего чемпиона (Фаза B), и
     бумажный цикл начинает использовать его. Подключение к брокеру — отдельный осознанный шаг.

Эквити счёта для сайзинга/брейкера считаем по РЕАЛИЗОВАННОМУ P&L (консервативно; нереализованный
маркер — в статусе). Демо-счёт по умолчанию `demo`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from geoanalytics.core.logging import get_logger

log = get_logger("futrader.paper")

DEFAULT_ACCOUNT = "demo"
PAPER_INTERVAL = "1h"
# Сессионная дисциплина (Фаза A): интрадей-сделка не переживает закрытие сессии / не висит овернайт.
SESSION_DISCIPLINE = True
FLAT_BEFORE_MIN = 15.0          # минут до закрытия → принудительный флэт (не под аукцион)
TRADE_EVENING = False           # вечерняя сессия FORTS (до 23:50 MSK) — по умолчанию off (основная)
TRADE_WEEKEND = True            # рабочие субботы MOEX — торгуем осторожно (тонкая ликвидность)
LOW_LIQUIDITY_RISK_SCALE = 0.5  # срез риска на тонкой сессии (выходные/вечер) — осторожность
# Барьер-выход (Tier A#1): живая позиция выходит по барьерам метки обучения (SL/TP/тайм-стоп).
BARRIER_EXIT = True
# Cost-aware гейт входа (Tier A#2): ожидаемый ход до take-profit должен покрывать издержки оборота
# с запасом — иначе сетап заведомо съест комиссия+спред (бьёт в PF<1). Мультипликатор запаса:
MIN_EDGE_COST_MULT = 1.5
# Режимы рынка (L5), в которых НЕ открываем новые позиции: в кризис волатильность/гэпы/проскальз.
# выше, а эдж там недоказан (мало кризисных данных). Выходы при этом разрешены — дериск, не вход.
BLOCKED_REGIMES = ("кризис",)
# Адаптивный режим в кризис (выбор пользователя): вместо 100% блокировки разрешить входы с
# сокращённым сайзингом (-60% риска), повышенной планкой P(win) ≥0.60 и фильтром издержек ≥2.5x.
ALLOW_CRISIS_ADAPTIVE = True
CRISIS_RISK_SCALE_MULT = 0.4
CRISIS_PWIN_FLOOR_MIN = 0.60
CRISIS_MIN_EDGE_COST_MULT = 2.5


@dataclass
class QualityGate:
    """Планка допуска стратегии к бумажной торговле (консервативно к шуму)."""

    min_lift: float = 0.0
    min_sharpe: float = 0.0
    min_taken: int = 20
    min_samples: int = 120
    allow_fallback: bool = True


def _bar_index(bars, ts) -> int | None:
    """Индекс бара с временной меткой `ts` в упорядоченном ряду (для восстановления входа). None —
    если бар не найден (роллнутый контракт/гэп в ряду) → барьер-выход тогда пропускаем."""
    if ts is None:
        return None
    for i in range(len(bars) - 1, -1, -1):     # с конца: вход обычно недавний
        if bars[i].ts == ts:
            return i
    return None


def regime_blocks_entry(regime, block_regimes=BLOCKED_REGIMES) -> bool:
    """Текущий режим рынка запрещает НОВЫЕ входы? (выходы всегда разрешены — дериск)."""
    return bool(regime is not None and regime.label in block_regimes)


def mark_to_market(positions, spec_map: dict) -> tuple[float, float]:
    """Реализованный и НЕреализованный P&L счёта (₽) по открытым позициям и спекам контрактов."""
    realized = unrealized = 0.0
    for p in positions:
        realized += p.realized_pnl or 0.0
        spec = spec_map.get(p.asset_code)
        if spec is not None and p.net_qty and p.avg_price and p.last_price:
            unrealized += spec.pnl_rub(p.last_price - p.avg_price, p.net_qty)
    return realized, unrealized


def _dispatch_risk_alert(account: str, reason: str) -> None:
    """Telegram-алерт о срабатывании kill-switch счёта (дедуп по часу; лог при сбое канала)."""
    from datetime import datetime as _dt

    from config.settings import get_settings
    from geoanalytics.alerts import channels
    from geoanalytics.alerts.engine import _insert_new
    from geoanalytics.alerts.rules import Alert
    from geoanalytics.storage.db import session_scope

    bucket = _dt.now(UTC).strftime("%Y-%m-%d-%H")
    alert = Alert(
        alert_type="health", severity="critical",
        title=f"Песочница остановлена [{account}]",
        message=(f"Kill-switch сработал: {reason}. Новые входы блокированы (выходы идут). "
                 "Снять: geo futures-intraday resume."),
        dedup_key=f"futrader_halt:{account}:{bucket}",
        payload={"account": account, "reason": reason},
    )
    try:
        with session_scope() as session:
            rec_id = _insert_new(session, alert)
        if rec_id is not None:
            channels.dispatch(alert, get_settings())
    except Exception as exc:  # noqa: BLE001 — без канала остаётся хотя бы лог
        log.error("futrader_risk_alert_failed", account=account, error=str(exc))


def _ensure_spec(session, spec_cache: dict, code: str):
    """Резолв спеки фронт-контракта по asset_code (кэш на цикл; сетевой вызов ISS лениво)."""
    from geoanalytics.analytics.history import _front_futures_secid
    from geoanalytics.futrader.data import fetch_contract_spec

    if code not in spec_cache:
        secid = _front_futures_secid(code)
        spec_cache[code] = fetch_contract_spec(secid) if secid else None
    return spec_cache[code]


def passes_gate(champ, gate: QualityGate) -> bool:
    """Чемпион из реестра проходит гейт качества?"""
    if champ is None:
        return False
    if (champ.lift or -1.0) <= gate.min_lift:
        return False
    if champ.sharpe is None or champ.sharpe < gate.min_sharpe:
        return False
    if (champ.n_taken or 0) < gate.min_taken:
        return False
    return (champ.n_samples or 0) >= gate.min_samples


@dataclass
class PaperResult:
    opened: int = 0
    closed: int = 0
    marked: int = 0
    considered: int = 0
    skipped_gate: int = 0
    blocked_breaker: int = 0
    blocked_regime: int = 0
    blocked_conviction: int = 0
    blocked_budget: int = 0
    blocked_halt: int = 0
    blocked_stale: int = 0
    blocked_liquidity: int = 0
    blocked_session: int = 0
    blocked_cost: int = 0
    blocked_size: int = 0        # мета-фильтр/риск дал qty≤0 (P(win)<порога или риск мал) — не вход
    no_signal: int = 0           # правило не дало направления на баре (dir_=0) — нечего открывать
    anomalies: int = 0
    session_flat: int = 0
    barrier_exits: int = 0
    halted: bool = False
    halt_reason: str = ""
    regime: str = ""
    qualified_strategies: tuple = ()
    equity: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    drawdown_pct: float = 0.0
    gross_margin: float = 0.0
    risk_scale: float = 1.0


def run_paper_cycle(session, *, account: str = DEFAULT_ACCOUNT, interval: str = PAPER_INTERVAL,
                    starting_cash: float = 100_000.0, target_risk_pct: float = 1.0,
                    max_dd_pct: float = 25.0, max_qty: int = 5,
                    max_gross_margin_pct: float = 50.0,
                    gate: QualityGate | None = None, tickers=None,
                    block_regimes: tuple = BLOCKED_REGIMES, limits=None,
                    use_conviction: bool = True,
                    min_conviction: float | None = None,
                    disagree_veto: float | None = None,
                    pwin_floor: float | None = None,
                    session_discipline: bool = SESSION_DISCIPLINE,
                    flat_before_min: float = FLAT_BEFORE_MIN,
                    trade_evening: bool = TRADE_EVENING,
                    trade_weekend: bool = TRADE_WEEKEND,
                    barrier_exit: bool = BARRIER_EXIT,
                    min_edge_cost_mult: float = MIN_EDGE_COST_MULT,
                    allow_crisis_adaptive: bool = ALLOW_CRISIS_ADAPTIVE) -> PaperResult:
    """Один бумажный цикл: квалифицированные чемпионы решают вход/выход на свежих барах.

    Идемпотентно по смыслу (повтор в тот же бар не открывает дубль — позиция уже есть). Сбой
    одного инструмента изолирован. Возвращает сводку (открыто/закрыто/маркеров/гейт/брейкер).
    """
    from geoanalytics.futrader.decisions import SIGNAL_FNS
    from geoanalytics.futrader.features import EdgeContext
    from geoanalytics.futrader.policy import load_policy
    from geoanalytics.storage.repositories import (
        FuturesModelRunRepository,
        FuturesPaperRepository,
        MarketRegimeRepository,
    )
    from geoanalytics.storage.seed import FUTURES

    gate = gate or QualityGate()
    repo = FuturesPaperRepository(session)
    reg = FuturesModelRunRepository(session)
    tickers = list(tickers) if tickers else list(FUTURES)
    res = PaperResult()

    from geoanalytics.futrader.risk_limits import (
        RiskLimits,
        daily_loss_breached,
        gross_margin_breached,
    )
    from geoanalytics.futrader.sizing import (
        portfolio_margin_used,
        risk_scale_for_drawdown,
    )
    from geoanalytics.storage.repositories import FuturesRiskStateRepository

    limits = limits or RiskLimits()

    # Текущий режим рынка (L5): в кризис входы либо блокируются (по умолчанию), либо масштабируются
    # под адаптивный режим (-60% риска, P(win)≥0.60, издержки≥2.5x).
    latest_regime = MarketRegimeRepository(session).latest()
    res.regime = latest_regime.label if latest_regime else ""
    is_crisis = bool(latest_regime and latest_regime.label in BLOCKED_REGIMES)
    if is_crisis and allow_crisis_adaptive:
        regime_blocked = False
    else:
        regime_blocked = is_crisis

    # Mark-to-market эквити: реализ.+нереализ. (спеки открытых позиций — для MTM и маржи).
    positions_now = repo.positions(account)
    spec_cache: dict = {}
    for p in positions_now:
        _ensure_spec(session, spec_cache, p.asset_code)
    realized_total, unrealized_now = mark_to_market(positions_now, spec_cache)
    equity = starting_cash + realized_total + unrealized_now
    curve = repo.equity_curve(account)

    risk_repo = FuturesRiskStateRepository(session)
    st = risk_repo.get(account)
    persisted_halt = bool(st and st.halted)
    resumed_at = getattr(st, "resumed_at", None) if st else None

    # Просадка относительно ПИКА (история после resumed_at + текущая точка) → брейкер + де-риск.
    valid_all_curve = [e for e in curve if not resumed_at or e.ts >= resumed_at]
    peak = max([e.peak_equity for e in valid_all_curve] + [equity], default=equity)
    drawdown_pct = (peak - equity) / peak * 100.0 if peak > 0 else 0.0
    margin_used = portfolio_margin_used(positions_now, spec_cache)
    res.drawdown_pct = round(drawdown_pct, 2)
    res.risk_scale = round(risk_scale_for_drawdown(drawdown_pct, max_dd_pct=max_dd_pct), 3)

    # Портфельный риск (C): корреляции инструментов + экспозиция → корр-осознанный лимит входа.
    from geoanalytics.analytics.portfolio import correlation_matrix
    from geoanalytics.futrader.portfolio_risk import (
        build_instrument_returns,
        build_intraday_returns,
        exposure_by_code,
    )
    try:
        rets_by_code = build_instrument_returns(session, tickers)
        corr_map = correlation_matrix(rets_by_code)
        try:
            intra_rets = build_intraday_returns(session, tickers, interval=interval, limit=120)
            if intra_rets:
                intra_corr = correlation_matrix(intra_rets)
                for k, v in intra_corr.items():
                    if k in corr_map:
                        corr_map[k] = round(0.5 * corr_map[k] + 0.5 * v, 4)
                    else:
                        corr_map[k] = round(v, 4)
        except Exception:  # noqa: BLE001 — интрадей блендинг опционален
            pass
    except Exception as exc:  # noqa: BLE001 — отсутствие рядов не валит цикл (лимит просто не давит)
        log.warning("paper_corr_load_failed", error=str(exc))
        corr_map = {}
    exposure_map = exposure_by_code(positions_now, spec_cache)

    # KILL-SWITCH (B). ЛАТЧИНГ только на событие УБЫТКА (дневной лимит) — нужен ручной resume,
    # чтобы человек разобрал причину.
    today = datetime.now(UTC).date()
    valid_today_curve = [e for e in curve if e.ts.date() == today and (not resumed_at or e.ts >= resumed_at)]
    day_peak = max([e.equity for e in valid_today_curve] + [equity], default=equity)
    if not persisted_halt and daily_loss_breached(
            day_peak, equity, max_daily_loss_pct=limits.max_daily_loss_pct):
        persisted_halt = True
        res.halt_reason = "дневной убыток"
        risk_repo.set_state(account, halted=True, reason=res.halt_reason)
        _dispatch_risk_alert(account, res.halt_reason)
    transient_block = (not persisted_halt) and gross_margin_breached(
        margin_used, equity, max_gross_margin_pct=limits.max_gross_margin_pct)
    if persisted_halt and not res.halt_reason:
        st = risk_repo.get(account)
        res.halt_reason = (st.reason if st and st.reason else "halt")
    elif transient_block:
        res.halt_reason = "брутто-маржа (транзиентно)"
    res.halted = persisted_halt                       # отражает ЛАТЧИНГ kill-switch

    from config.settings import get_settings as _get_settings
    _s = _get_settings()
    eff_min_conviction = _s.futrader_min_conviction if min_conviction is None else min_conviction
    eff_disagree_veto = _s.futrader_disagree_veto if disagree_veto is None else disagree_veto
    eff_pwin_floor = _s.futrader_pwin_floor if pwin_floor is None else pwin_floor

    ctx = _CycleCtx(session=session, repo=repo, account=account, interval=interval,
                    equity=equity, target_risk_pct=target_risk_pct, max_qty=max_qty,
                    broke=drawdown_pct >= max_dd_pct,
                    spec_cache=spec_cache, edge=EdgeContext(session), term_cache={},
                    regime_blocked=regime_blocked, risk_scale=res.risk_scale,
                    margin_used=margin_used, max_gross_margin_pct=max_gross_margin_pct,
                    halted=persisted_halt or transient_block, limits=limits,
                    corr=corr_map, exposure=exposure_map,
                    use_conviction=use_conviction, conviction_cache={},
                    min_conviction=eff_min_conviction, disagree_veto=eff_disagree_veto,
                    pwin_floor=eff_pwin_floor,
                    session_discipline=session_discipline, flat_before_min=flat_before_min,
                    trade_evening=trade_evening, trade_weekend=trade_weekend,
                    barrier_exit=barrier_exit, min_edge_cost_mult=min_edge_cost_mult,
                    is_crisis=is_crisis)

    qualified: list[str] = []
    champs_by_strat: dict = {}
    for strat in SIGNAL_FNS:
        champ = reg.champion(source=strat, asset_code=None, interval=interval)
        champs_by_strat[strat] = champ
        if not passes_gate(champ, gate):
            res.skipped_gate += 1
            continue
        model = load_policy(None, strat)
        if model is None:
            res.skipped_gate += 1
            continue
        qualified.append(strat)

    # Fallback: Если ВСЕ стратегии отсечены жестким гейтом, но включен allow_fallback —
    # берём лучшую стратегию по Sharpe с редуцированным риском (консервативный полу-риск)
    if not qualified and getattr(gate, "allow_fallback", False) and champs_by_strat:
        best_strat = None
        best_sharpe = -999.0
        for strat, champ in champs_by_strat.items():
            if champ and (champ.n_samples or 0) >= max(10, int(gate.min_samples / 2)):
                sh = champ.sharpe if champ.sharpe is not None else -1.0
                if sh > best_sharpe:
                    best_sharpe = sh
                    best_strat = strat
        if best_strat:
            model = load_policy(None, best_strat)
            if model is not None:
                qualified.append(best_strat)
                ctx.risk_scale *= 0.5  # редуцируем целевой риск для фоллбэка
                log.info("paper_fallback_qualified", strategy=best_strat, sharpe=best_sharpe)

    for strat in qualified:
        champ = champs_by_strat.get(strat)
        model = load_policy(None, strat)
        if model is None or champ is None:
            continue
        for tk in tickers:
            try:
                _step_instrument(ctx, model, champ, tk, strat, res)
            except Exception as exc:  # noqa: BLE001 — один инструмент не валит цикл
                log.warning("paper_step_failed", ticker=tk, strategy=strat, error=str(exc))

    res.qualified_strategies = tuple(qualified)
    # Финальный mark-to-market после сделок + снимок эквити в трек-рекорд (идемпотентно за час).
    final_positions = repo.positions(account)
    realized_final, unrealized_final = mark_to_market(final_positions, ctx.spec_cache)
    res.realized_pnl = round(realized_final, 2)
    res.unrealized_pnl = round(unrealized_final, 2)
    res.equity = round(starting_cash + realized_final + unrealized_final, 2)
    res.gross_margin = round(portfolio_margin_used(final_positions, ctx.spec_cache), 2)
    open_n = sum(1 for p in final_positions if p.net_qty)
    snap_peak = max(peak, res.equity)
    res.drawdown_pct = round((snap_peak - res.equity) / snap_peak * 100.0, 2) if snap_peak > 0 \
        else 0.0
    snap_ts = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    repo.record_equity(account, snap_ts, equity=res.equity, realized_pnl=res.realized_pnl,
                       unrealized_pnl=res.unrealized_pnl, open_positions=open_n,
                       peak_equity=snap_peak, drawdown_pct=res.drawdown_pct,
                       gross_margin=res.gross_margin)
    log.info("paper_cycle", account=account, opened=res.opened, closed=res.closed,
             gated_out=res.skipped_gate, blocked_regime=res.blocked_regime,
             blocked_conviction=res.blocked_conviction,
             blocked_budget=res.blocked_budget, blocked_halt=res.blocked_halt,
             anomalies=res.anomalies, halted=res.halted, halt_reason=res.halt_reason,
             regime=res.regime, drawdown_pct=res.drawdown_pct,
             risk_scale=res.risk_scale, qualified=res.qualified_strategies, equity=res.equity)
    return res


@dataclass
class _CycleCtx:
    """Общий контекст бумажного цикла (счёт/настройки/кэш спек) для шага по инструменту."""

    session: object
    repo: object
    account: str
    interval: str
    equity: float
    target_risk_pct: float
    max_qty: int
    broke: bool
    spec_cache: dict
    edge: object = None
    term_cache: dict = None
    regime_blocked: bool = False
    risk_scale: float = 1.0
    margin_used: float = 0.0
    max_gross_margin_pct: float = 50.0
    halted: bool = False
    limits: object = None
    corr: dict = None
    exposure: dict = None
    use_conviction: bool = True
    conviction_cache: dict = None
    min_conviction: float = 0.15
    disagree_veto: float = 0.0
    pwin_floor: float = 0.0
    session_discipline: bool = True
    flat_before_min: float = 15.0
    trade_evening: bool = False
    trade_weekend: bool = True
    barrier_exit: bool = True
    min_edge_cost_mult: float = 1.5
    is_crisis: bool = False


def _step_instrument(ctx: _CycleCtx, model, champ, tk: str, strat: str, res: PaperResult) -> None:
    """Шаг по одному инструменту: вход/выход/маркер по последнему бару. Мутирует `res` и БД."""
    from geoanalytics.futrader.continuous import continuous_series
    from geoanalytics.futrader.data import _asset_code_for
    from geoanalytics.futrader.decisions import enrich_features, extract_features
    from geoanalytics.futrader.execution import (
        fill_price,
        slippage_liquidity_mult,
        slippage_ticks_for_qty,
    )
    from geoanalytics.futrader.exits import barrier_exit
    from geoanalytics.futrader.instrument_features import term_structure_map
    from geoanalytics.futrader.labeling import bar_return_std, round_trip_cost_rub
    from geoanalytics.futrader.portfolio_risk import correlation_scale
    from geoanalytics.futrader.risk_limits import (
        bar_stale,
        entry_bar_too_stale,
        price_jump_anomaly,
        thin_liquidity,
    )
    from geoanalytics.futrader.session import (
        crossed_session,
        entry_allowed,
        force_flat_due,
        low_liquidity_session,
    )
    from geoanalytics.futrader.signals import DIRECTIONAL_FNS, apply_strategy
    from geoanalytics.futrader.sizing import margin_budget_qty, position_margin, position_size

    repo, account, interval = ctx.repo, ctx.account, ctx.interval
    code = _asset_code_for(tk)
    series = continuous_series(ctx.session, tk, interval=interval)
    if len(series.bars) < 30:
        return
    res.considered += 1
    closes = [b.close for b in series.bars]
    highs = [getattr(b, "high", b.close) for b in series.bars]
    lows = [getattr(b, "low", b.close) for b in series.bars]
    volumes = [getattr(b, "volume", None) for b in series.bars]
    opens = [getattr(b, "open", None) for b in series.bars]
    signals = apply_strategy(DIRECTIONAL_FNS[strat], strat, closes,
                             opens=opens, highs=highs, lows=lows)
    last = len(series.bars) - 1
    price = closes[last]
    last_ts = series.bars[last].ts
    # Объективный вход (A1–A4): инструмент-уровневые доказательства считаем ОДИН раз за цикл
    # (rule-независимы — те же для всех стратегий этого инструмента), кэш по asset_code.
    if ctx.use_conviction and ctx.conviction_cache is not None and code not in ctx.conviction_cache:
        from geoanalytics.futrader.conviction import gather_entry_drivers
        sbs = {s: apply_strategy(fn, s, closes, opens=opens, highs=highs, lows=lows)
               for s, fn in DIRECTIONAL_FNS.items()}
        ctx.conviction_cache[code] = gather_entry_drivers(
            ctx.session, ticker=tk, asset_code=code, signals_by_strat=sbs, idx=last,
            intraday=interval != "1d")
    # Полный serve-вектор (как в обучении, без train/serve skew): TA+объём+свечи + эдж+контанго.
    if code not in ctx.term_cache:
        ctx.term_cache[code] = term_structure_map(ctx.session, code, interval)
    serve_feats = enrich_features(
        extract_features(closes, highs, lows, last, volumes=volumes, opens=opens),
        last_ts, code, edge=ctx.edge, term_map=ctx.term_cache[code], interval=interval)
    pos = repo.position(account, code, interval, strat)
    net = pos.net_qty if pos else 0
    realized = pos.realized_pnl if pos else 0.0

    spec = _ensure_spec(ctx.session, ctx.spec_cache, code)
    if spec is None:
        return
    target = signals[last]
    direction = 1 if target > 0 else (-1 if target < 0 else 0)
    # Аномалии данных (B): устаревший бар или скачок цены → не входить (битый фид/тик).
    entry_anomaly = entry_stale = entry_thin = entry_session_block = False
    vol_z = serve_feats.get("vol_z")
    if ctx.limits is not None:
        now_ = datetime.now(UTC)
        prev_close = closes[last - 1] if last >= 1 else None
        entry_anomaly = bar_stale(last_ts, now_,
                                  max_hours=ctx.limits.max_bar_staleness_hours) or \
            price_jump_anomaly(prev_close, price, max_move_pct=ctx.limits.max_price_jump_pct)
        # Сессионный гейт: вне торговой сессии (выходные/праздники) свежих баров нет → последний
        # бар устаревает относительно интервала → НЕ открывать (иначе фиктивный вход по залежавшейся
        # цене с гэпом на открытии). Выходы остаются разрешены (дериск). Чинит выходную торговлю.
        entry_stale = entry_bar_too_stale(last_ts, now_, interval=interval,
                                          mult=ctx.limits.entry_max_bar_age_mult)
        # Гейт ликвидности: тонкая сессия (нет объёма / vol_z ниже порога) → пропуск входа.
        entry_thin = thin_liquidity(volumes[last], vol_z, min_vol_z=ctx.limits.min_entry_vol_z)
    # Сессионная дисциплина (Фаза A): в окне закрытия / вне торговой сессии новые входы НЕ открываем
    # (выходы остаются разрешены ниже — это лишь блок входа на конкретном баре).
    if ctx.session_discipline:
        entry_session_block = not entry_allowed(
            last_ts, flat_before_min=ctx.flat_before_min, evening=ctx.trade_evening,
            allow_weekend=ctx.trade_weekend)

    def _open(dir_: int, realized_now: float) -> None:
        """Открыть позицию в сторону dir_ (+лонг/−шорт) с vol-target сайзингом, под брейкером."""
        if ctx.halted:
            res.blocked_halt += 1
            repo.log_trade(account=account, asset_code=code, interval=interval, source=strat,
                           action="buy" if dir_ > 0 else "sell", signed_qty=0, price=price,
                           reason="halt")
            return
        if entry_anomaly:
            res.anomalies += 1
            repo.log_trade(account=account, asset_code=code, interval=interval, source=strat,
                           action="buy" if dir_ > 0 else "sell", signed_qty=0, price=price,
                           reason="anomaly")
            return
        if entry_stale:
            res.blocked_stale += 1
            repo.log_trade(account=account, asset_code=code, interval=interval, source=strat,
                           action="buy" if dir_ > 0 else "sell", signed_qty=0, price=price,
                           reason="stale")
            return
        if entry_thin:
            res.blocked_liquidity += 1
            repo.log_trade(account=account, asset_code=code, interval=interval, source=strat,
                           action="buy" if dir_ > 0 else "sell", signed_qty=0, price=price,
                           reason="liquidity")
            return
        if ctx.broke:
            res.blocked_breaker += 1
            repo.log_trade(account=account, asset_code=code, interval=interval, source=strat,
                           action="buy" if dir_ > 0 else "sell", signed_qty=0, price=price,
                           reason="breaker")
            return
        if ctx.regime_blocked:
            res.blocked_regime += 1
            repo.log_trade(account=account, asset_code=code, interval=interval, source=strat,
                           action="buy" if dir_ > 0 else "sell", signed_qty=0, price=price,
                           reason="regime")
            return
        if entry_session_block:                  # окно закрытия / вне сессии — не открываем
            res.blocked_session += 1
            repo.log_trade(account=account, asset_code=code, interval=interval, source=strat,
                           action="buy" if dir_ > 0 else "sell", signed_qty=0, price=price,
                           reason="session")
            return
        # ОБЪЕКТИВНЫЙ ВХОД (A5): правило лишь ПРЕДЛОЖИЛО сторону — требуем согласия совокупности
        # независимых доказательств (консенсус+мульти-ТФ+базис+сценарий). Не согласны/слабо → блок;
        # иначе размер ∝ уверенности. Гейт МЕЖДУ режим-гейтом и мета-фильтром (до model.score).
        conv = None
        if ctx.use_conviction and ctx.conviction_cache is not None:
            from geoanalytics.futrader.conviction import entry_conviction
            conv = entry_conviction(dir_, ctx.conviction_cache.get(code, []),
                                    min_conviction=ctx.min_conviction,
                                    disagree_veto=ctx.disagree_veto)
            if not conv.passes:
                res.blocked_conviction += 1
                repo.log_trade(account=account, asset_code=code, interval=interval, source=strat,
                               action="buy" if dir_ > 0 else "sell", signed_qty=0, price=price,
                               reason="conviction", conviction=round(conv.conviction, 4),
                               conviction_drivers=conv.as_breakdown())
                return
        p = model.score(serve_feats, dir_)
        vol = bar_return_std(closes, last) or 0.0
        # Cost-aware гейт (Tier A#2): ожидаемый ход до take-profit (+UP_MULT·σ) должен покрывать
        # издержки полного оборота (комиссия+проскальзывание) с запасом. В кризис требуем ≥2.5x.
        from geoanalytics.futrader.exits import UP_MULT
        exp_move_rub = spec.pnl_rub(UP_MULT * vol * price, 1)
        cost_rub = round_trip_cost_rub(spec, 1)
        eff_cost_mult = (max(ctx.min_edge_cost_mult, CRISIS_MIN_EDGE_COST_MULT)
                         if getattr(ctx, "is_crisis", False) else ctx.min_edge_cost_mult)
        if vol > 0 and exp_move_rub < eff_cost_mult * cost_rub:
            res.blocked_cost += 1
            repo.log_trade(account=account, asset_code=code, interval=interval, source=strat,
                           action="buy" if dir_ > 0 else "sell", signed_qty=0, price=price,
                           reason="cost")
            return
        # Целевой риск масштабируем просадкой (плавный де-риск) И уверенностью совокупности (A5),
        # а также срезаем на -60% во время адаптивного кризисного режима.
        conv_mult = conv.risk_multiplier if conv is not None else 1.0
        liq_mult = (LOW_LIQUIDITY_RISK_SCALE
                    if low_liquidity_session(last_ts, evening=ctx.trade_evening) else 1.0)
        crisis_mult = CRISIS_RISK_SCALE_MULT if getattr(ctx, "is_crisis", False) else 1.0
        risk = ctx.target_risk_pct * ctx.risk_scale * conv_mult * liq_mult * crisis_mult
        # Абсолютный пол порога мета-фильтра: в кризис требуем P(win) ≥ 0.60 (высокая уверенность)
        eff_floor = (max(ctx.pwin_floor, CRISIS_PWIN_FLOOR_MIN)
                     if getattr(ctx, "is_crisis", False) else ctx.pwin_floor)
        eff_threshold = (min(champ.threshold, eff_floor) if eff_floor > 0
                         else champ.threshold)
        qty = position_size(p, equity=ctx.equity, price=price, vol_fraction=vol, spec=spec,
                            threshold=eff_threshold, target_risk_pct=risk, max_qty=ctx.max_qty)
        # ПОЛ P(win) активен и сетап прошёл порог, но симметричный Келли (payoff=1) занулил размер
        # при p<0.5 → берём минимум 1 контракт. Осознанный «торгуем с допустимым риском»: вход с
        # P(win)≥floor отрицателен по ожиданию, но нужны живые сделки/исходы на демо.
        if qty <= 0 and ctx.pwin_floor > 0 and p >= eff_threshold:
            qty = 1
        if qty <= 0:
            res.blocked_size += 1        # мета-фильтр (P(win)<порога) или риск→0 контрактов
            repo.log_trade(account=account, asset_code=code, interval=interval, source=strat,
                           action="buy" if dir_ > 0 else "sell", signed_qty=0, price=price,
                           reason="size", p_win=round(p, 4))
            return
        # Корр-осознанный лимит (C): не наращивать скоррелированную одностороннюю экспозицию бука.
        if ctx.corr and ctx.exposure:
            qty = int(qty * correlation_scale(code, dir_, ctx.exposure, ctx.corr))
            if qty <= 0:
                res.blocked_size += 1
                return
        # Портфельный бюджет плеча: урезаем размер под валовую маржу счёта (или блок, если 0).
        capped = margin_budget_qty(qty, equity=ctx.equity, margin_used=ctx.margin_used, spec=spec,
                                   max_gross_margin_pct=ctx.max_gross_margin_pct)
        if capped <= 0:
            res.blocked_budget += 1
            repo.log_trade(account=account, asset_code=code, interval=interval, source=strat,
                           action="buy" if dir_ > 0 else "sell", signed_qty=0, price=price,
                           reason="budget")
            return
        signed = dir_ * capped
        # Реализм исполнения (A): проскальзывание против трейдера + комиссия в реализованный P&L.
        # Слипидж масштабируется ликвидностью (тонкая сессия → дороже исполнение, без оптимизма).
        side = "buy" if dir_ > 0 else "sell"
        slip = slippage_ticks_for_qty(capped, liquidity_mult=slippage_liquidity_mult(vol_z))
        entry_px = fill_price(price, side, tick_size=spec.tick_size, slip_ticks=slip)
        entry_fee = spec.fee * capped
        repo.upsert_position(account, code, interval, strat, net_qty=signed, avg_price=entry_px,
                             realized_pnl=realized_now - entry_fee, last_price=price)
        repo.log_trade(account=account, asset_code=code, interval=interval, source=strat,
                       ts=last_ts, action=side, signed_qty=signed, price=round(entry_px, 4),
                       p_win=round(p, 4), reason="entry",
                       conviction=round(conv.conviction, 4) if conv is not None else None,
                       conviction_drivers=conv.as_breakdown() if conv is not None else None)
        ctx.margin_used += position_margin(spec, signed)
        res.opened += 1

    def _close(reason: str) -> None:
        """Закрыть позицию (знаковый P&L: шорт прибылен на падении) со слипиджем+комиссией."""
        nonlocal realized
        exit_side = "sell" if net > 0 else "buy"
        exit_px = fill_price(price, exit_side, tick_size=spec.tick_size,
                             slip_ticks=slippage_ticks_for_qty(
                                 net, liquidity_mult=slippage_liquidity_mult(vol_z)))
        exit_fee = spec.fee * abs(net)
        pnl = spec.pnl_rub(exit_px - (pos.avg_price or exit_px), net) - exit_fee
        realized += pnl
        repo.upsert_position(account, code, interval, strat, net_qty=0, avg_price=None,
                             realized_pnl=realized, last_price=price)
        repo.log_trade(account=account, asset_code=code, interval=interval, source=strat,
                       ts=last_ts, action=exit_side, signed_qty=-net, price=round(exit_px, 4),
                       realized_pnl=round(pnl, 2), reason=reason)

    # Сессионная дисциплина (Фаза A): интрадей-сделка не переживает закрытие сессии и не висит
    # овернайт. force_flat_due — бар в окне закрытия/вне сессии; crossed_session — вход был в
    # ПРОШЛОЙ торговой сессии. ВЫХОД всегда разрешён (дериск) — форсируем флэт до обычной логики.
    if ctx.session_discipline and net != 0:
        flat_due = force_flat_due(last_ts, flat_before_min=ctx.flat_before_min,
                                  evening=ctx.trade_evening, allow_weekend=ctx.trade_weekend)
        if not flat_due:
            entry_ts = repo.last_entry_ts(account, code, interval, strat)
            flat_due = entry_ts is not None and crossed_session(entry_ts, last_ts)
        if flat_due:
            _close("session_flat")
            res.session_flat += 1
            return

    # Барьер-осознанный выход (Tier A#1): живая позиция выходит по ТЕМ ЖЕ барьерам, под которые
    # обучалась метка (SL −σ / TP +σ / тайм-стоп) — не «висит до флипа». Согласует serve с train.
    # Путь и σ входа восстанавливаем из баров (без миграции): entry_ts → индекс входа в ряду.
    if ctx.barrier_exit and net != 0:
        entry_ts = repo.last_entry_ts(account, code, interval, strat)
        entry_idx = _bar_index(series.bars, entry_ts) if entry_ts is not None else None
        if entry_idx is not None and entry_idx < last:
            entry_vol = bar_return_std(closes, entry_idx) or 0.0
            decision = barrier_exit(
                1 if net > 0 else -1, closes[entry_idx], entry_vol,
                highs[entry_idx + 1:last + 1], lows[entry_idx + 1:last + 1])
            if decision.should_exit:
                _close(decision.reason)
                res.barrier_exits += 1
                return

    if net == 0:
        if direction != 0:
            _open(direction, realized)
        else:
            res.no_signal += 1                       # правило молчит на баре — открывать нечего
    elif direction == 0 or (direction > 0) != (net > 0):
        # выход в флэт или РАЗВОРОТ: сначала закрываем (P&L знаковый), при развороте открываем.
        _close("exit")
        res.closed += 1
        if direction != 0:                       # разворот → открываем противоположную сторону
            _open(direction, realized)
    else:
        repo.upsert_position(account, code, interval, strat, net_qty=net, avg_price=pos.avg_price,
                             realized_pnl=realized, last_price=price)
        res.marked += 1
