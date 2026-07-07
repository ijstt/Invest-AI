"""Трек 2 / T2.3: лог торговых решений + контекст-признаки + разметка исходов.

Чистые ядра (тестируемые без БД): `extract_features` — снимок индикаторов на момент решения;
`decisions_from_signals` — точки действия политики (вход/выход) с признаками; `label_decisions` —
forward-исход через горизонт (win/loss/flat). DB-раннер `log_decisions` гоняет базовую политику по
непрерывному ряду, размечает и пишет в `futures_decisions`. Это обучающая выборка для T2.4: каждое
решение = (контекст, действие, исход), на которой потом дообучается малая модель.

Признаки переиспользуют общий слой индикаторов (`analytics.indicators`/`analytics.backtest`) — без
дублирования. Базовые политики (sma_cross/momentum/rsi/macd/bollinger) дают сигнал 0/1 (лонг/вне);
действия логируются в моменты смены сигнала. Ограничения v1: лонг/выход (без шортовых входов),
исход по forward-доходности базиса (без проскальзывания/комиссии — это в симуляторе T2.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from geoanalytics.analytics import backtest as bt
from geoanalytics.analytics.candlesticks import candle_signals
from geoanalytics.analytics.indicators import (
    macd_hist_series,
    returns_pct,
    rsi,
    sma,
    volatility,
)
from geoanalytics.futrader.labeling import bar_return_std, round_trip_cost_rub, triple_barrier

# имя политики → функция сигналов 0/1 (лонг-онли). Ключи == DIRECTIONAL_FNS. Свечные (candles)
# принимают OHLC через keyword — вызывать строго через signals.apply_strategy, не fn(closes).
SIGNAL_FNS = {
    "sma_cross": bt.sma_cross_signals,
    "momentum": bt.momentum_signals,
    "rsi": bt.rsi_signals,
    "macd": bt.macd_cross_signals,
    "bollinger": bt.bollinger_signals,
    "candles": candle_signals,
}


@dataclass
class Decision:
    """Одно решение политики в точке действия + (опц.) размеченный исход."""

    ts: datetime
    action: str                      # buy/sell/hold/close
    signed_qty: int                  # знаковая ставка направления (buy +, sell/close −)
    price: float                     # цена решения (close бара)
    features: dict                   # контекст-признаки на момент решения
    contract_secid: str | None = None
    horizon_bars: int | None = None
    outcome_ts: datetime | None = None
    outcome_return_pct: float | None = None
    outcome_pnl_rub: float | None = None
    label: str | None = None         # win/loss/flat (None — не дозрело)


def extract_features(closes: list[float], highs: list[float], lows: list[float],
                     i: int, *, range_window: int = 20, volumes: list | None = None,
                     opens: list | None = None) -> dict:
    """Снимок индикаторов на баре `i` (по префиксу до `i` включительно). Чистая.

    Пропускает признаки, недоступные в прогреве (значение None). Все — из общего слоя индикаторов.
    `volumes` (Пул 6) — если задан, добавляет `vol_z`: z-оценку объёма бара относительно окна.
    `opens` — если задан, добавляет свечные паттерны (`cdl_wick`/`cdl_engulf`): разворотная
    микроформа свечи как признаки мета-фильтра (решает PBO/калибровка, не зашиты в правило).
    """
    prefix = closes[: i + 1]
    feats: dict[str, float] = {}

    def put(key: str, value: float | None) -> None:
        if value is not None:
            feats[key] = round(float(value), 4)

    put("ret_1", returns_pct(prefix, 1))
    put("ret_5", returns_pct(prefix, 5))
    put("ret_20", returns_pct(prefix, 20))
    put("rsi_14", rsi(prefix, 14))
    put("vol_20", volatility(prefix, 20))
    sma20 = sma(prefix, 20)
    if sma20:
        put("sma_gap_20", (prefix[-1] / sma20 - 1.0) * 100)   # отрыв цены от SMA20, %
    hist = macd_hist_series(prefix)
    if hist and hist[-1] is not None:
        put("macd_hist", hist[-1])
    # положение цены в диапазоне последних range_window баров high/low ∈ [0, 1].
    hi = highs[max(0, i - range_window + 1): i + 1]
    lo = lows[max(0, i - range_window + 1): i + 1]
    if hi and lo:
        top, bot = max(hi), min(lo)
        if top > bot:
            put("range_pos", (closes[i] - bot) / (top - bot))
    # Объёмная микроструктура: z-оценка объёма бара относительно окна (всплеск/затухание).
    if volumes is not None and i >= range_window:
        win = [v for v in volumes[i - range_window:i] if v is not None]
        if len(win) >= 5 and volumes[i] is not None:
            mean = sum(win) / len(win)
            sd = (sum((v - mean) ** 2 for v in win) / len(win)) ** 0.5
            if sd > 0:
                put("vol_z", (volumes[i] - mean) / sd)
    # Свечные паттерны (нужны opens): хвост свечи и поглощение — классическая разворотная форма.
    if opens is not None and i < len(opens) and opens[i] is not None:
        o, c, hh, ll = opens[i], closes[i], highs[i], lows[i]
        rng = hh - ll
        if rng > 0:
            upper, lower = hh - max(o, c), min(o, c) - ll
            put("cdl_wick", (lower - upper) / rng)   # +нижний хвост (молот) / −верхний (звезда)
        if i >= 1 and opens[i - 1] is not None:
            po, pc = opens[i - 1], closes[i - 1]
            if c > o and pc < po and c >= po and o <= pc:
                put("cdl_engulf", 1.0)               # бычье поглощение
            elif c < o and pc > po and c <= po and o >= pc:
                put("cdl_engulf", -1.0)              # медвежье поглощение
            else:
                put("cdl_engulf", 0.0)
    return feats


def enrich_features(features: dict, ts, asset_code: str, *, edge, term_map=None,
                    interval: str = "1h") -> dict:
    """Дополнить TA-признаки контекстом (Пул A/2/6): эдж Трека 1 + инструмент + контанго + час.

    Единая точка для лога решений И для бумажного исполнения — устраняет train/serve skew (модель
    обучена на полном векторе, в проде должна получать тот же). `interval` управляет анти-lookahead:
    внутридневной бар (≠"1d") получает дневной эдж строго за D−1. Мутирует и возвращает `features`.
    """
    from geoanalytics.futrader.features import INSTRUMENT_CODES

    # Внутридневной бар не знает дневной агрегат СВОЕГО дня (режим/сентимент/доходность по закрытию)
    # → берём строго D−1; дневной бар (1d) контемпорален закрытию → день d.
    features.update(edge.features_at(ts, intraday=interval != "1d"))
    # Per-instrument news-сентимент базового актива (Tier B/Фаза D) — отдельно от рыночного скаляра.
    features.update(edge.asset_features_at(ts, asset_code, intraday=interval != "1d"))
    instr = INSTRUMENT_CODES.get(asset_code)
    if instr is not None:
        features["instr"] = float(instr)
    if term_map and ts in term_map:
        features["term_slope"] = term_map[ts]             # контанго/бэквордация
    features["hour"] = float(ts.hour)                     # час сессии (FORTS день/вечер)
    return features


def decisions_from_signals(bars: list, signals: list[int], *, qty: int = 1,
                           range_window: int = 20, directional: bool = False) -> list[Decision]:
    """Точки действия политики: лог решения в момент смены сигнала.

    `bars` — объекты с ts/close/high/low (например `ContBar`). Признаки снимаются на баре действия.
    - long-only (`directional=False`): `signals[i]` ∈ {0,1}; 0→1 = buy (+qty), 1→0 = close (−qty).
    - двусторонний (`directional=True`, Pool 2): `signals[i]` ∈ {−1,0,1}; логируем ВХОД/РАЗВОРОТ в
      сторону s (buy +qty при s>0, sell −qty при s<0); выход в флэт (s==0) — не направленная ставка.
    """
    n = min(len(bars), len(signals))
    closes = [b.close for b in bars[:n]]
    highs = [getattr(b, "high", b.close) for b in bars[:n]]
    lows = [getattr(b, "low", b.close) for b in bars[:n]]
    volumes = [getattr(b, "volume", None) for b in bars[:n]]
    opens = [getattr(b, "open", None) for b in bars[:n]]
    out: list[Decision] = []
    prev = 0
    for i in range(n):
        s = signals[i]
        if s == prev:
            continue
        if directional:
            if s == 0:                         # выход в флэт — не направленная ставка
                prev = s
                continue
            action, signed = ("buy", qty) if s > 0 else ("sell", -qty)
        elif s == 1 and prev == 0:
            action, signed = "buy", qty
        elif s == 0 and prev == 1:
            action, signed = "close", -qty
        else:                                  # на случай иных кодировок — пропуск
            prev = s
            continue
        out.append(Decision(
            ts=bars[i].ts, action=action, signed_qty=signed, price=closes[i],
            features=extract_features(closes, highs, lows, i, range_window=range_window,
                                      volumes=volumes, opens=opens),
            contract_secid=getattr(bars[i], "contract_secid", None)))
        prev = s
    return out


def label_decisions(decisions: list[Decision], bars: list, spec, *,
                    horizon_bars: int = 12, flat_eps_pct: float = 0.1,
                    method: str = "triple_barrier", up_mult: float = 1.5,
                    down_mult: float = 1.5, vol_window: int = 20,
                    cost_aware: bool = True, slippage_ticks: float = 1.0,
                    session_aware: bool = False, flat_before_min: float = 15.0,
                    trade_evening: bool = False, trade_weekend: bool = True) -> list[Decision]:
    """Разметить исход каждого решения. Чистая (мутирует поля).

    `method`:
      - `"triple_barrier"` (Фаза A, дефолт) — López de Prado: take-profit/stop-loss/время, барьеры
        ±k·σ от волатильности входа; метка по первому касанию (учитывает путь и торгуемость).
      - `"horizon"` (T2.3) — знак forward-доходности базиса на `horizon_bars`-м баре.
    `cost_aware` (Пул 3, дефолт) — `outcome_pnl_rub` = ЧИСТЫЙ P&L (вал − издержки полного оборота:
    2×комиссия + проскальзывание), и метка win/loss по ЗНАКУ ЧИСТОГО P&L: мелкий ход, съеденный
    издержками, размечается как loss. `outcome_return_pct` — валовая доходность базиса (справочно).
    Решения без полного горизонта (у конца ряда) остаются неразмеченными.
    """
    n = len(bars)
    ts_to_idx = {b.ts: k for k, b in enumerate(bars)}
    closes = [b.close for b in bars]
    highs = [getattr(b, "high", b.close) for b in bars]
    lows = [getattr(b, "low", b.close) for b in bars]
    # Сессионная дисциплина (Фаза A): вертикальный барьер НЕ переживает закрытие сессии — выход на
    # ПЕРВОМ баре форсфлэта (как paper). flat_flags[j] = бар j в окне закрытия/вне сессии.
    flat_flags = None
    if session_aware:
        from geoanalytics.futrader.session import force_flat_due
        flat_flags = [force_flat_due(b.ts, flat_before_min=flat_before_min, evening=trade_evening,
                                     allow_weekend=trade_weekend) for b in bars]
    for d in decisions:
        i = ts_to_idx.get(d.ts)
        if i is None:
            continue
        # Потолок вертикали: при session_aware — 1-й бар форсфлэта после входа (None → сессия входа
        # не завершена в данных, не метим, чтобы не выдумывать исход). Иначе — полный бар-горизонт.
        if flat_flags is not None:
            fb = next((j for j in range(i + 1, n) if flat_flags[j]), None)
            if fb is None:
                continue
            cap = min(i + horizon_bars, fb, n - 1)
        elif i + horizon_bars >= n:
            continue
        else:
            cap = i + horizon_bars
        if cap <= i:
            continue
        sign = 1 if d.signed_qty >= 0 else -1
        if method == "triple_barrier":
            vol = bar_return_std(closes, i, window=vol_window)
            if vol is None or vol <= 0:
                continue                      # без оценки σ метку не ставим (прогрев)
            outcome = triple_barrier(highs, lows, closes, i, sign, horizon=horizon_bars,
                                     up_mult=up_mult, down_mult=down_mult, vol=vol,
                                     flat_eps=flat_eps_pct / 100, end_idx=cap)
            ti, ret, label = outcome.touch_idx, outcome.return_pct, outcome.label
        else:
            ti = cap
            ret = (bars[ti].close / d.price - 1) * 100 if d.price else 0.0
            label = "flat" if abs(ret) < flat_eps_pct else (
                "win" if (ret * sign) > 0 else "loss")
        delta = d.price * ret / 100
        gross_pnl = spec.pnl_rub(delta, d.signed_qty)
        if cost_aware:
            net_pnl = gross_pnl - round_trip_cost_rub(spec, d.signed_qty,
                                                      slippage_ticks=slippage_ticks)
            if label != "flat":               # чистый знак решает win/loss (вал может не покрыть)
                label = "win" if net_pnl > 0 else "loss"
            pnl = net_pnl
        else:
            pnl = gross_pnl
        d.horizon_bars = horizon_bars
        d.outcome_ts = bars[ti].ts
        d.outcome_return_pct = round(ret, 4)
        d.outcome_pnl_rub = round(pnl, 2)
        d.label = label
    return decisions


def _to_row(d: Decision, asset_code: str, interval: str, source: str) -> dict:
    return {
        "ts": d.ts, "asset_code": asset_code, "interval": interval,
        "contract_secid": d.contract_secid, "source": source, "action": d.action,
        "signed_qty": d.signed_qty, "price": d.price, "features": d.features,
        "horizon_bars": d.horizon_bars, "outcome_ts": d.outcome_ts,
        "outcome_return_pct": d.outcome_return_pct, "outcome_pnl_rub": d.outcome_pnl_rub,
        "label": d.label,
    }


@dataclass
class LogResult:
    decisions: list[Decision] = field(default_factory=list)
    stored: int = 0
    labeled: int = 0
    wins: int = 0

    @property
    def win_rate(self) -> float | None:
        return round(self.wins / self.labeled, 3) if self.labeled else None


def log_decisions(session, ticker: str, interval: str = "1h", *, source: str = "sma_cross",
                  qty: int = 1, horizon_bars: int = 12, label_method: str = "triple_barrier",
                  edge=None, enrich: bool = True, directional: bool = True) -> LogResult:
    """DB-раннер: непрерывный ряд → сигналы политики → решения+признаки → разметка → запись.

    `source` ∈ SIGNAL_FNS. `directional` (Pool 2, дефолт) — двусторонние сигналы (лонг+шорт);
    иначе лонг-онли. `label_method` — triple_barrier (Фаза A) / horizon. `enrich` подмешивает
    признаки-эдж Трека 1 (режим/сентимент/кросс-актив) + код инструмента (`edge` — готовый
    `EdgeContext`, иначе строится). Идемпотентно (upsert по точке); RETURNING-счёт строк.
    """
    from geoanalytics.analytics.history import _front_futures_secid
    from geoanalytics.futrader.continuous import continuous_series
    from geoanalytics.futrader.data import _asset_code_for, fetch_contract_spec
    from geoanalytics.futrader.features import EdgeContext
    from geoanalytics.futrader.signals import DIRECTIONAL_FNS, apply_strategy
    from geoanalytics.storage.repositories import FuturesDecisionRepository

    if source not in SIGNAL_FNS:
        raise ValueError(f"source должен быть из {list(SIGNAL_FNS)}")
    asset_code = _asset_code_for(ticker)
    secid = _front_futures_secid(asset_code)
    spec = fetch_contract_spec(secid) if secid else None
    if spec is None:                       # нет спеки → нейтральная (исход в ₽ = 0, но % считается)
        from geoanalytics.futrader.execution import ContractSpec
        spec = ContractSpec(secid=secid or asset_code, tick_size=1.0, tick_value=0.0,
                            initial_margin=0.0)
    series = continuous_series(session, ticker, interval=interval)
    if not series.bars:
        return LogResult()
    closes = [b.close for b in series.bars]
    opens = [getattr(b, "open", b.close) for b in series.bars]
    highs = [getattr(b, "high", b.close) for b in series.bars]
    lows = [getattr(b, "low", b.close) for b in series.bars]
    fn = DIRECTIONAL_FNS[source] if directional else SIGNAL_FNS[source]
    signals = apply_strategy(fn, source, closes, opens=opens, highs=highs, lows=lows)
    decisions = decisions_from_signals(series.bars, signals, qty=qty, directional=directional)
    label_decisions(decisions, series.bars, spec, horizon_bars=horizon_bars, method=label_method,
                    session_aware=interval != "1d")
    if enrich and decisions:
        from geoanalytics.futrader.instrument_features import term_structure_map

        ctx = edge if edge is not None else EdgeContext(session)
        term = term_structure_map(session, asset_code, interval)   # {ts: контанго%}
        for d in decisions:
            enrich_features(d.features, d.ts, asset_code, edge=ctx, term_map=term,
                            interval=interval)
    rows = [_to_row(d, asset_code, interval, source) for d in decisions]
    stored = FuturesDecisionRepository(session).upsert_many(rows)
    labeled = [d for d in decisions if d.label is not None]
    wins = sum(1 for d in labeled if d.label == "win")
    return LogResult(decisions=decisions, stored=stored, labeled=len(labeled), wins=wins)


def log_cross_sectional_decisions(session, *, tickers, interval: str = "1h",
                                  source: str = "xsec_mom", qty: int = 1, horizon_bars: int = 12,
                                  label_method: str = "triple_barrier", edge=None,
                                  lookback: int = 20) -> LogResult:
    """DB-раннер кросс-секционной стратегии (Пул 9/E): выровнять инструменты по времени → ранг-
    сигналы → решения+признаки+разметка по КАЖДОМУ инструменту под общим `source`. Сигнал зависит
    от всех (лонг-топ/шорт-боттом по моментуму). Через ту же оценку/PBO, что и остальные.
    """
    from geoanalytics.analytics.history import _front_futures_secid
    from geoanalytics.futrader.continuous import continuous_series
    from geoanalytics.futrader.data import _asset_code_for, fetch_contract_spec
    from geoanalytics.futrader.execution import ContractSpec
    from geoanalytics.futrader.features import EdgeContext
    from geoanalytics.futrader.instrument_features import term_structure_map
    from geoanalytics.futrader.signals import cross_sectional_signals
    from geoanalytics.storage.repositories import FuturesDecisionRepository

    # Загрузить ряды и выровнять по ОБЩИМ меткам времени (кросс-секция требует одновременных баров).
    bymap: dict[str, dict] = {}
    for tk in tickers:
        s = continuous_series(session, tk, interval=interval)
        if s.bars:
            bymap[_asset_code_for(tk)] = {b.ts: b for b in s.bars}
    if len(bymap) < 3:
        return LogResult()
    common: set | None = None
    for m in bymap.values():
        common = set(m) if common is None else (common & set(m))
    common_ts = sorted(common or [])
    if len(common_ts) <= lookback:
        return LogResult()
    aligned = {c: [bymap[c][t] for t in common_ts] for c in bymap}
    closes_by_code = {c: [b.close for b in aligned[c]] for c in bymap}
    signals_by_code = cross_sectional_signals(closes_by_code, lookback=lookback)

    ctx = edge if edge is not None else EdgeContext(session)
    repo = FuturesDecisionRepository(session)
    res = LogResult()
    for code, bars in aligned.items():
        secid = _front_futures_secid(code)
        spec = fetch_contract_spec(secid) if secid else None
        if spec is None:
            spec = ContractSpec(secid=secid or code, tick_size=1.0, tick_value=0.0,
                                initial_margin=0.0)
        decisions = decisions_from_signals(bars, signals_by_code[code], qty=qty, directional=True)
        label_decisions(decisions, bars, spec, horizon_bars=horizon_bars, method=label_method,
                        session_aware=interval != "1d")
        if not decisions:
            continue
        term = term_structure_map(session, code, interval)
        for d in decisions:
            enrich_features(d.features, d.ts, code, edge=ctx, term_map=term, interval=interval)
        rows = [_to_row(d, code, interval, source) for d in decisions]
        res.stored += repo.upsert_many(rows)
        labeled = [d for d in decisions if d.label is not None]
        res.labeled += len(labeled)
        res.wins += sum(1 for d in labeled if d.label == "win")
        res.decisions.extend(decisions)
    return res
