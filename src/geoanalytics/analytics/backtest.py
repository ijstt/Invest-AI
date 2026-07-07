"""Бэктест торговых сигналов — чистые функции над рядами цен.

Назначение: оценить, давал ли сигнал (технический или новостной) полезное
преимущество на истории. Как и `indicators.py`, ядро написано на чистом Python
(списки float), без pandas: детерминированно и легко тестируется. Ряд закрытий
ожидается в хронологическом порядке (старое → новое).

Модель исполнения — long/flat без плеча и комиссий (грубая, но честная оценка
направления сигнала). Чтобы избежать заглядывания в будущее, позиция на бар t
определяется сигналом, известным на баре t-1 (`held = [0] + signals[:-1]`).

Сигналы возвращаются как список int той же длины, что и `closes`: 1 — лонг, 0 — вне рынка.
"""

from __future__ import annotations

import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from itertools import product

from geoanalytics.analytics.indicators import bollinger, macd_hist_series, rsi


# --------------------------------------------------------------------------- #
# Генераторы сигналов (чистые функции над рядом закрытий).
# --------------------------------------------------------------------------- #
def sma_cross_signals(closes: list[float], fast: int = 20, slow: int = 50) -> list[int]:
    """Пересечение скользящих средних: лонг, пока быстрая SMA выше медленной."""
    out = []
    for i in range(len(closes)):
        end = i + 1
        if end < slow:
            out.append(0)
            continue
        fast_ma = sum(closes[end - fast:end]) / fast
        slow_ma = sum(closes[end - slow:end]) / slow
        out.append(1 if fast_ma > slow_ma else 0)
    return out


def momentum_signals(closes: list[float], lookback: int = 20) -> list[int]:
    """Моментум: лонг, если цена выше, чем `lookback` баров назад."""
    out = []
    for i in range(len(closes)):
        if i < lookback:
            out.append(0)
            continue
        out.append(1 if closes[i] > closes[i - lookback] else 0)
    return out


def rsi_signals(closes: list[float], window: int = 14,
                low: float = 30.0, high: float = 55.0) -> list[int]:
    """Возврат к среднему по RSI: входим при перепроданности (<low), держим до >high."""
    out, pos = [], 0
    for i in range(len(closes)):
        value = rsi(closes[:i + 1], window)
        if value is None:
            out.append(0)
            continue
        if pos == 0 and value < low:
            pos = 1
        elif pos == 1 and value > high:
            pos = 0
        out.append(pos)
    return out


def macd_cross_signals(closes: list[float], fast: int = 12, slow: int = 26,
                       signal: int = 9) -> list[int]:
    """Тренд по MACD: лонг, пока гистограмма положительна (линия выше сигнальной).

    Использует выровненный по барам ряд `macd_hist_series`; в прогреве (None) — вне рынка.
    """
    hist = macd_hist_series(closes, fast, slow, signal)
    return [1 if (h is not None and h > 0) else 0 for h in hist]


def bollinger_signals(closes: list[float], window: int = 20, k: float = 2.0) -> list[int]:
    """Возврат к среднему по Боллинджеру: вход у нижней полосы, выход на средней.

    Лонг открывается, когда цена ≤ нижней полосы (перепроданность), и держится до
    возврата к средней (SMA). Состояние переносится между барами, как в `rsi_signals`.
    """
    out, pos = [], 0
    for i in range(len(closes)):
        band = bollinger(closes[:i + 1], window, k)
        if band is None:
            out.append(0)
            continue
        lower, mid, _upper = band
        price = closes[i]
        if pos == 0 and price <= lower:
            pos = 1
        elif pos == 1 and price >= mid:
            pos = 0
        out.append(pos)
    return out


def sentiment_signals(dates: list[date], scored: list[tuple[date, float]],
                      decay_days: int = 5) -> list[int]:
    """Новостной сигнал: лонг, если суммарный сентимент свежих новостей положителен.

    `scored` — список (дата публикации, score ∈ [-1, 1]). Для каждой торговой даты
    учитываются новости за последние `decay_days` дней (включительно). Сигнал = 1,
    если сумма их score > 0, иначе 0.
    """
    out = []
    for d in dates:
        total = sum(
            s for (pub, s) in scored
            if pub <= d and (d - pub).days <= decay_days
        )
        out.append(1 if total > 0 else 0)
    return out


def sentiment_gate(dates: list[date], scored: list[tuple[date, float]],
                   decay_days: int = 5, min_score: float = 0.0) -> list[int]:
    """Тональный фильтр (B6): 1, если суммарный сентимент свежих новостей ≥ `min_score`.

    В отличие от `sentiment_signals` (строго > 0, самостоятельная стратегия), это
    «разрешающий» фильтр-оверлей: при `min_score=0` блокирует только дни с негативным
    новостным фоном, пропуская нейтраль и позитив. Накладывается на ценовой сигнал
    логическим И (`combine_and`) — позиция держится, только если и цена, и фон «за».
    """
    out = []
    for d in dates:
        total = sum(
            s for (pub, s) in scored
            if pub <= d and (d - pub).days <= decay_days
        )
        out.append(1 if total >= min_score else 0)
    return out


def combine_and(*signal_lists: list[int]) -> list[int]:
    """Поэлементное И сигналов 0/1: лонг только при единогласии всех рядов."""
    if not signal_lists:
        return []
    return [min(vals) for vals in zip(*signal_lists, strict=True)]


# Реестр стратегий по ценовому ряду (имя → функция сигналов).
PRICE_STRATEGIES = {
    "sma_cross": sma_cross_signals,
    "momentum": momentum_signals,
    "rsi": rsi_signals,
    "macd_cross": macd_cross_signals,
    "bollinger": bollinger_signals,
}

# Стратегии, которым нужен полный OHLC (свечные паттерны): сигнатура та же
# fn(closes, **params), но opens/highs/lows DB-раннер подставляет partial'ом.
def _candle_signals(closes, **params):
    from geoanalytics.analytics.candlesticks import candle_signals

    return candle_signals(closes, **params)


OHLC_STRATEGIES = {"candles": _candle_signals}


# --------------------------------------------------------------------------- #
# Ядро бэктеста и метрики.
# --------------------------------------------------------------------------- #
@dataclass
class Trade:
    """Одна сделка лонг: индексы и цены входа/выхода, доходность."""

    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float

    @property
    def ret_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        return round((self.exit_price / self.entry_price - 1) * 100, 2)


@dataclass
class BacktestResult:
    """Итог бэктеста: доходность стратегии, метрики риска, сделки."""

    bars: int = 0
    total_return_pct: float = 0.0          # чистая (после издержек)
    total_return_gross_pct: float = 0.0    # валовая (без издержек), для прозрачности
    cost_bps: float = 0.0                  # издержка за одну сторону сделки, базисные пункты
    buy_hold_return_pct: float = 0.0
    index_return_pct: float | None = None  # buy&hold бенчмарка (IMOEX) за тот же период
    alpha_pct: float | None = None         # доходность стратегии минус доходность индекса
    cagr_pct: float | None = None
    sharpe: float | None = None
    sortino: float | None = None          # доходность на единицу downside-риска
    calmar: float | None = None           # CAGR / макс. просадка
    max_drawdown_pct: float = 0.0
    hit_rate: float | None = None
    profit_factor: float | None = None    # сумма прибылей / |сумма убытков| по сделкам
    avg_win_pct: float | None = None
    avg_loss_pct: float | None = None
    num_trades: int = 0
    exposure: float = 0.0
    equity_curve: list[float] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in {"equity_curve", "trades"}}
        d["num_trades"] = len(self.trades)
        return d


def buy_hold_return_pct(closes: list[float]) -> float | None:
    """Доходность пассивного удержания ряда, в процентах. None — мало данных."""
    if len(closes) < 2 or not closes[0]:
        return None
    return round((closes[-1] / closes[0] - 1) * 100, 2)


def _max_drawdown(equity: list[float]) -> float:
    """Максимальная просадка кривой капитала, в процентах (положительное число)."""
    peak, mdd = equity[0] if equity else 1.0, 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return round(mdd * 100, 2)


def _extract_trades(closes: list[float], held: list[int]) -> list[Trade]:
    """Сделки из ряда удерживаемых позиций (held[t] — лонг на отрезке t-1→t)."""
    trades: list[Trade] = []
    in_pos, entry_idx = False, 0
    for t in range(1, len(closes)):
        if not in_pos and held[t] == 1:
            in_pos, entry_idx = True, t - 1
        elif in_pos and held[t] == 0:
            trades.append(Trade(entry_idx, t - 1, closes[entry_idx], closes[t - 1]))
            in_pos = False
    if in_pos:
        last = len(closes) - 1
        trades.append(Trade(entry_idx, last, closes[entry_idx], closes[last]))
    return trades


def run(closes: list[float], signals: list[int],
        periods_per_year: int = 252, cost_bps: float = 0.0) -> BacktestResult:
    """Прогоняет сигнал по ряду закрытий и считает метрики.

    `signals[t]` — желаемая позиция, наблюдаемая на баре t; исполняется со
    следующего бара (`held = [0] + signals[:-1]`), без заглядывания в будущее.

    `cost_bps` — транзакционная издержка (комиссия + проскальзывание) за ОДНУ
    сторону сделки, в базисных пунктах (10 б.п. = 0.1%). Списывается на каждой
    смене позиции (`held[t] != held[t-1]`): вход 0→1 и выход 1→0 — каждый одна
    сторона, у завершённой сделки набегает round-trip = 2×. Открытую на конце
    позицию `_extract_trades` закрывает принудительно, но exit-издержку за неё НЕ
    берём (сделка фактически не закрыта). Метрики риска (Sharpe/CAGR/maxDD/equity)
    считаются по ЧИСТОЙ кривой; `total_return_gross_pct` — та же стратегия без
    издержек, для сравнения «грязная vs чистая».
    """
    result = BacktestResult(bars=len(closes), cost_bps=cost_bps)
    if len(closes) < 2 or len(signals) != len(closes):
        return result

    cost_rate = cost_bps / 1e4
    held = [0] + signals[:-1]  # позиция, удерживаемая на отрезке t-1→t
    equity, strat_rets = [1.0], []
    gross_equity = 1.0
    for t in range(1, len(closes)):
        prev = closes[t - 1]
        bar_ret = (closes[t] / prev - 1) if prev else 0.0
        gross_r = held[t] * bar_ret
        gross_equity *= (1 + gross_r)
        r = gross_r
        if cost_rate and held[t] != held[t - 1]:  # позиция сменилась → одна сторона сделки
            r -= cost_rate
        strat_rets.append(r)
        equity.append(equity[-1] * (1 + r))

    result.equity_curve = equity
    result.total_return_pct = round((equity[-1] - 1) * 100, 2)
    result.total_return_gross_pct = round((gross_equity - 1) * 100, 2)
    if closes[0]:
        result.buy_hold_return_pct = round((closes[-1] / closes[0] - 1) * 100, 2)
    result.max_drawdown_pct = _max_drawdown(equity)
    result.exposure = round(sum(held) / len(held), 3)

    # CAGR из общей доходности и числа лет.
    years = len(strat_rets) / periods_per_year
    if years > 0 and equity[-1] > 0:
        result.cagr_pct = round((equity[-1] ** (1 / years) - 1) * 100, 2)

    # Коэффициент Шарпа (безрисковая ставка 0, годовое масштабирование).
    if len(strat_rets) >= 2:
        mean = sum(strat_rets) / len(strat_rets)
        var = sum((r - mean) ** 2 for r in strat_rets) / (len(strat_rets) - 1)
        std = var ** 0.5
        if std > 0:
            result.sharpe = round(mean / std * (periods_per_year ** 0.5), 2)
        # Сортино: как Шарп, но риск = только просадочные доходности (downside deviation
        # к цели 0). Не штрафует за рост — честнее к асимметрии прибыли и убытка.
        downside = sum(min(r, 0.0) ** 2 for r in strat_rets) / len(strat_rets)
        dd = downside ** 0.5
        if dd > 0:
            result.sortino = round(mean / dd * (periods_per_year ** 0.5), 2)

    # Кальмар: годовая доходность на единицу худшей просадки.
    if result.cagr_pct is not None and result.max_drawdown_pct > 0:
        result.calmar = round(result.cagr_pct / result.max_drawdown_pct, 2)

    result.trades = _extract_trades(closes, held)
    result.num_trades = len(result.trades)
    if result.trades:
        rets = [tr.ret_pct for tr in result.trades]
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r < 0]
        result.hit_rate = round(len(wins) / len(rets), 3)
        if wins:
            result.avg_win_pct = round(sum(wins) / len(wins), 2)
        if losses:
            result.avg_loss_pct = round(sum(losses) / len(losses), 2)
        # Profit-factor по доходностям сделок (брутто, без издержек — отдельный срез).
        gross_loss = -sum(losses)
        if gross_loss > 0:
            result.profit_factor = round(sum(wins) / gross_loss, 2)
    return result


# --------------------------------------------------------------------------- #
# Подбор параметров и walk-forward (out-of-sample) — защита от переобучения.
#
# Подобрать «лучшие» параметры на всей истории легко, но это самообман: метрика
# завышается подгонкой под прошлый шум (overfit). Честная оценка — walk-forward:
# параметры подбираются на in-sample окне, а результат снимается на следующем,
# НЕ виденном при подборе, out-of-sample окне; окна катятся по истории. Разрыв
# между in-sample и out-of-sample доходностью (walk-forward efficiency) и есть мера
# переобучения. Ядро чистое: сигналы считаются по полному ряду один раз и режутся
# по индексам — каждый `signals[t]` зависит лишь от `closes[:t+1]`, поэтому нарезка
# окна не привносит заглядывания, а прогрев индикаторов на границе остаётся «тёплым».
# --------------------------------------------------------------------------- #
# Цель оптимизации: имя → извлечение метрики из результата (None — не определена).
_OBJECTIVES: dict[str, Callable[[BacktestResult], float | None]] = {
    "total_return": lambda r: r.total_return_pct,
    "sharpe": lambda r: r.sharpe,
    "sortino": lambda r: r.sortino,
    "calmar": lambda r: r.calmar,
    "cagr": lambda r: r.cagr_pct,
}


def param_grid(grid: dict[str, list]) -> list[dict]:
    """Декартово произведение сетки параметров → список словарей-комбинаций.

    Пустая сетка даёт один пустой набор (стратегия с дефолтными параметрами).
    """
    if not grid:
        return [{}]
    keys = list(grid)
    return [dict(zip(keys, combo, strict=True))
            for combo in product(*(grid[k] for k in keys))]


@dataclass
class OptimizeResult:
    """Итог перебора сетки: лучшая комбинация + отранжированная таблица."""

    best_params: dict
    best_score: float | None
    objective: str
    leaderboard: list[tuple[dict, float | None]]  # (params, score), по убыванию score


def _score_of(objective: str) -> Callable[[BacktestResult], float | None]:
    try:
        return _OBJECTIVES[objective]
    except KeyError as exc:
        raise ValueError(
            f"Неизвестная цель: {objective}. Доступно: {sorted(_OBJECTIVES)}"
        ) from exc


def _strategy_fn(strategy: str | Callable[..., list[int]]) -> Callable[..., list[int]]:
    """Резолв стратегии: имя из реестра или готовый callable (OHLC через partial)."""
    if callable(strategy):
        return strategy
    if strategy not in PRICE_STRATEGIES:
        raise ValueError(
            f"Стратегия {strategy} не параметризуема. "
            f"Доступно: {sorted(PRICE_STRATEGIES) + sorted(OHLC_STRATEGIES)}"
        )
    return PRICE_STRATEGIES[strategy]


def optimize(closes: list[float], strategy: str, grid: dict[str, list], *,
             objective: str = "sharpe", cost_bps: float = 0.0,
             periods_per_year: int = 252,
             valid: Callable[[dict], bool] | None = None) -> OptimizeResult:
    """Перебор сетки параметров на ВСЁМ ряде closes (in-sample оптимизация).

    Возвращает лучшую комбинацию по `objective` и отранжированный leaderboard.
    ВНИМАНИЕ: это оптимизация с заглядыванием на весь ряд — оценка завышена; для
    честной оценки используйте `walk_forward`. `valid` отсеивает нелепые комбинации
    (напр. fast≥slow). Комбинации с неопределённой метрикой (None) — в хвосте.
    """
    fn = _strategy_fn(strategy)
    score_of = _score_of(objective)
    board: list[tuple[dict, float | None]] = []
    for params in param_grid(grid):
        if valid and not valid(params):
            continue
        res = run(closes, fn(closes, **params), periods_per_year, cost_bps)
        board.append((params, score_of(res)))
    # None — худший; среди чисел — по убыванию.
    board.sort(key=lambda ps: (ps[1] is not None, ps[1] or 0.0), reverse=True)
    best_params, best_score = board[0] if board else ({}, None)
    return OptimizeResult(best_params, best_score, objective, board)


def make_folds(n: int, train: int, test: int,
               anchored: bool = False) -> list[tuple[int, int, int, int]]:
    """Окна walk-forward: список (train_start, train_end, test_start, test_end).

    Границы — полуинтервалы [start, end). Test-окна не пересекаются и идут подряд.
    `anchored=True` — заякоренный режим: train всегда от начала истории (растущее
    окно); иначе — скользящее окно фиксированной длины `train`.
    """
    folds: list[tuple[int, int, int, int]] = []
    start = 0
    while start + train + test <= n:
        train_end = start + train
        folds.append((0 if anchored else start, train_end, train_end, train_end + test))
        start += test
    return folds


@dataclass
class WalkForwardFold:
    """Один шаг walk-forward: окна, подобранные параметры, IS- и OOS-доходность."""

    train_start: int
    train_end: int
    test_start: int
    test_end: int
    best_params: dict
    train_return_pct: float    # лучшая доходность на in-sample (оптимистична — подгонка)
    test_return_pct: float     # реализованная out-of-sample доходность (честная)


@dataclass
class WalkForwardResult:
    """Итог walk-forward: честная склеенная OOS-кривая против IS-оптимизма."""

    strategy: str
    objective: str
    train: int
    test: int
    anchored: bool
    folds: list[WalkForwardFold] = field(default_factory=list)
    oos_return_pct: float = 0.0        # компаунд OOS по всем фолдам (честная)
    oos_buy_hold_pct: float = 0.0      # buy&hold актива на покрытом OOS-отрезке
    oos_index_pct: float | None = None  # buy&hold бенчмарка (IMOEX) на OOS-отрезке
    oos_alpha_pct: float | None = None  # честная OOS-доходность минус индекс
    oos_sharpe: float | None = None
    oos_max_drawdown_pct: float = 0.0
    is_return_pct: float = 0.0         # компаунд лучших IS-доходностей (оптимизм/overfit)
    efficiency: float | None = None    # OOS/IS прирост: <1 — переобучение, <0 — слив OOS
    oos_equity: list[float] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in {"oos_equity", "folds"}}
        d["num_folds"] = len(self.folds)
        return d


def _pick_best(combos: list[dict], sig_cache: dict, closes: list[float],
               start: int, end: int, score_of, periods_per_year: int,
               cost_bps: float) -> tuple[dict, BacktestResult]:
    """Лучшая комбинация на окне [start, end) по предрассчитанным сигналам."""
    best: tuple[dict, float | None, BacktestResult] | None = None
    for params in combos:
        sig = sig_cache[tuple(sorted(params.items()))]
        res = run(closes[start:end], sig[start:end], periods_per_year, cost_bps)
        score = score_of(res)
        # Первая комбинация — фолбэк (на случай, если у всех метрика None);
        # дальше берём строго лучшую определённую метрику.
        if best is None or (score is not None and (best[1] is None or score > best[1])):
            best = (params, score, res)
    return best[0], best[2]


def walk_forward(closes: list[float], strategy: str | Callable[..., list[int]],
                 grid: dict[str, list], *,
                 train: int, test: int, objective: str = "sharpe",
                 cost_bps: float = 0.0, periods_per_year: int = 252,
                 anchored: bool = False,
                 valid: Callable[[dict], bool] | None = None) -> WalkForwardResult:
    """Walk-forward: на каждом фолде подбор на train, исполнение на test, склейка.

    На каждом окне параметры подбираются по `objective` на in-sample отрезке, затем
    лучшая комбинация исполняется на следующем out-of-sample отрезке. OOS-доходности
    компаундятся в честную кривую; `efficiency` = прирост OOS / прирост IS — мера
    переобучения (≈1 — стратегия держит вне выборки, <0.5 — сильная подгонка).
    """
    fn = _strategy_fn(strategy)
    score_of = _score_of(objective)
    name = strategy if isinstance(strategy, str) else getattr(
        strategy, "__name__", "custom")
    result = WalkForwardResult(strategy=name, objective=objective,
                               train=train, test=test, anchored=anchored)

    combos = [p for p in param_grid(grid) if not (valid and not valid(p))]
    folds_idx = make_folds(len(closes), train, test, anchored)
    if not combos or not folds_idx:
        return result

    # Сигналы каждой комбинации считаем ОДИН раз по полному ряду (тёплый прогрев),
    # дальше режем по индексам окна — без пересчёта и без заглядывания.
    sig_cache = {tuple(sorted(p.items())): fn(closes, **p) for p in combos}

    oos_equity = [1.0]
    is_growth = 1.0
    for (trs, tre, ts, te) in folds_idx:
        best_params, res_is = _pick_best(
            combos, sig_cache, closes, trs, tre, score_of, periods_per_year, cost_bps)
        sig = sig_cache[tuple(sorted(best_params.items()))]
        res_oos = run(closes[ts:te], sig[ts:te], periods_per_year, cost_bps)

        result.folds.append(WalkForwardFold(
            train_start=trs, train_end=tre, test_start=ts, test_end=te,
            best_params=best_params,
            train_return_pct=res_is.total_return_pct,
            test_return_pct=res_oos.total_return_pct,
        ))
        is_growth *= 1 + res_is.total_return_pct / 100
        # Склейка OOS-кривой: переносим помесячные приросты окна на общий капитал.
        eq = res_oos.equity_curve
        for i in range(1, len(eq)):
            factor = eq[i] / eq[i - 1] if eq[i - 1] else 1.0
            oos_equity.append(oos_equity[-1] * factor)

    result.oos_equity = oos_equity
    result.oos_return_pct = round((oos_equity[-1] - 1) * 100, 2)
    result.is_return_pct = round((is_growth - 1) * 100, 2)
    result.oos_max_drawdown_pct = _max_drawdown(oos_equity)
    if is_growth > 1:  # прирост IS положителен → отношение приростов осмысленно
        result.efficiency = round((oos_equity[-1] - 1) / (is_growth - 1), 3)

    # Buy&hold на покрытом OOS-отрезке (от первого test_start до последнего бара).
    first_ts, last_te = folds_idx[0][2], folds_idx[-1][3]
    if closes[first_ts]:
        result.oos_buy_hold_pct = round(
            (closes[last_te - 1] / closes[first_ts] - 1) * 100, 2)

    # Sharpe честной склеенной кривой (барные доходности OOS).
    rets = [oos_equity[i] / oos_equity[i - 1] - 1
            for i in range(1, len(oos_equity)) if oos_equity[i - 1]]
    if len(rets) >= 2:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        std = var ** 0.5
        if std > 0:
            result.oos_sharpe = round(mean / std * (periods_per_year ** 0.5), 2)
    return result


# Дефолтные сетки перебора по стратегиям + предикаты допустимости комбинаций.
DEFAULT_GRIDS: dict[str, dict[str, list]] = {
    "sma_cross": {"fast": [10, 20, 30], "slow": [50, 100, 200]},
    "momentum": {"lookback": [10, 20, 40, 60]},
    "rsi": {"low": [25, 30, 35], "high": [50, 55, 60]},
    "macd_cross": {"fast": [8, 12], "slow": [21, 26], "signal": [9]},
    "bollinger": {"window": [20, 30], "k": [2.0, 2.5, 3.0]},
    "candles": {"hold": [5, 10, 20], "trend": [10, 20]},
}

GRID_VALIDATORS: dict[str, Callable[[dict], bool]] = {
    "sma_cross": lambda p: p["fast"] < p["slow"],
    "rsi": lambda p: p["low"] < p["high"],
    "macd_cross": lambda p: p["fast"] < p["slow"],
}


# --------------------------------------------------------------------------- #
# DB-раннер: достаёт историю из БД и прогоняет выбранную стратегию.
# --------------------------------------------------------------------------- #
def _price_series_with_dates(session, asset_id: int, limit: int = 5000):
    """Ряд (даты, закрытия) по активу в хронологическом порядке."""
    from sqlalchemy import asc, select

    from geoanalytics.storage.models import Price

    rows = session.execute(
        select(Price.ts, Price.close)
        .where(Price.asset_id == asset_id, Price.interval == "1d")
        .order_by(asc(Price.ts)).limit(limit)
    ).all()
    dates = [ts.date() for ts, _ in rows]
    closes = [float(c) for _, c in rows]
    return dates, closes


def _ohlc_series(session, asset_id: int, limit: int = 5000):
    """Ряды (opens, highs, lows) теми же датами/порядком, что closes.

    Пропуски OHL (старые срезы) заполняются close — свеча вырождается в доджи
    без диапазона и паттернами не ловится (честная деградация).
    """
    from sqlalchemy import asc, select

    from geoanalytics.storage.models import Price

    rows = session.execute(
        select(Price.open, Price.high, Price.low, Price.close)
        .where(Price.asset_id == asset_id, Price.interval == "1d")
        .order_by(asc(Price.ts)).limit(limit)
    ).all()
    opens = [float(o) if o is not None else float(c) for o, _h, _l, c in rows]
    highs = [float(h) if h is not None else float(c) for _o, h, _l, c in rows]
    lows = [float(lo) if lo is not None else float(c) for _o, _h, lo, c in rows]
    return opens, highs, lows


def _ohlc_strategy_fn(name: str, opens, highs, lows) -> Callable[..., list[int]]:
    """Замыкает OHLC-ряды в стратегию со стандартной сигнатурой fn(closes, **params)."""
    base = OHLC_STRATEGIES[name]

    def fn(closes: list[float], **params) -> list[int]:
        return base(closes, opens=opens, highs=highs, lows=lows, **params)

    fn.__name__ = name
    return fn


def _benchmark_return_pct(session, first: date, last: date) -> float | None:
    """Buy&hold бенчмарка IMOEX на отрезке [first, last] по торговым датам индекса.

    None, если индекс не загружен (graceful — alpha просто не считается) или на
    отрезке меньше двух точек. Серия индекса невелика (~год дневных), грузим целиком
    и режем по датам в Python — без возни с tz-границами datetime.
    """
    from sqlalchemy import asc, select

    from geoanalytics.storage.models import Asset, Price
    from geoanalytics.storage.seed import BENCHMARK_TICKER

    idx = session.scalars(
        select(Asset).where(Asset.ticker == BENCHMARK_TICKER)
    ).first()
    if idx is None:
        return None
    rows = session.execute(
        select(Price.ts, Price.close)
        .where(Price.asset_id == idx.id, Price.interval == "1d")
        .order_by(asc(Price.ts))
    ).all()
    closes = [float(c) for ts, c in rows if first <= ts.date() <= last]
    return buy_hold_return_pct(closes)


def _article_scores(session, asset_id: int) -> list[tuple[date, float]]:
    """Сентимент-оценки новостей, связанных с активом: (ТОРГОВАЯ дата, score).

    Дата — `trading_effective_date`, а не дата публикации: новость после закрытия
    сессии относится к следующему дню (Б3 — иначе тональный фильтр заглядывал бы
    в будущее и систематически завышал alpha B6-стратегий).
    """
    from sqlalchemy import select

    from geoanalytics.core.dates import trading_effective_date
    from geoanalytics.core.types import EntityType
    from geoanalytics.storage.models import Article, ArticleEntity

    rows = session.execute(
        select(Article.published_at, Article.sentiment_score)
        .join(ArticleEntity, ArticleEntity.article_id == Article.id)
        .where(
            ArticleEntity.entity_type == EntityType.ASSET.value,
            ArticleEntity.entity_id == asset_id,
            Article.published_at.is_not(None),
            Article.sentiment_score.is_not(None),
        )
    ).all()
    return [(trading_effective_date(pub), float(score)) for pub, score in rows]


# B3: TTL-кэш бэктестов — ask/дашборд зовут backtest_asset на каждый ответ, а это grid+проход
# по истории. История за день не меняется, поэтому короткий кэш по ключу (тикер, стратегия)
# убирает повторный пересчёт без устаревания результата. Процесс один — простого dict хватает.
_BT_CACHE: dict[tuple, tuple[float, object]] = {}
_BT_TTL = 300.0


def backtest_asset_cached(ticker: str, strategy: str = "sma_cross",
                          ttl: float = _BT_TTL) -> BacktestResult | None:
    """Кэшированный `backtest_asset` (дефолтные параметры) для горячих путей (ask/дашборд)."""
    key = (ticker.upper(), strategy)
    now = _time.monotonic()
    hit = _BT_CACHE.get(key)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]
    res = backtest_asset(ticker, strategy=strategy)
    _BT_CACHE[key] = (now, res)
    return res


def backtest_asset(ticker: str, strategy: str = "sma_cross",
                   params: dict | None = None,
                   cost_bps: float | None = None,
                   sentiment_filter: bool = False) -> BacktestResult | None:
    """Бэктест стратегии по истории актива из БД. None — если актив не найден.

    Стратегии: sma_cross, momentum, rsi (по ценам) и sentiment (по новостям).
    `params` передаются в генератор сигналов (напр. {"fast": 10, "slow": 30}).
    `cost_bps` — издержка за сторону сделки; None → значение из настроек.
    `sentiment_filter` (B6) — наложить тональный фильтр поверх ценового сигнала
    (лонг только при неотрицательном новостном фоне); неприменим к стратегии sentiment.
    """
    from sqlalchemy import select

    from config.settings import get_settings
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset

    params = params or {}
    if cost_bps is None:
        cost_bps = get_settings().backtest_cost_bps
    with session_scope() as session:
        asset = session.scalars(
            select(Asset).where(Asset.ticker == ticker.upper())
        ).first()
        if asset is None:
            return None

        dates, closes = _price_series_with_dates(session, asset.id)
        if strategy == "sentiment":
            signals = sentiment_signals(dates, _article_scores(session, asset.id), **params)
        elif strategy in PRICE_STRATEGIES or strategy in OHLC_STRATEGIES:
            if strategy in OHLC_STRATEGIES:
                fn = _ohlc_strategy_fn(strategy, *_ohlc_series(session, asset.id))
            else:
                fn = PRICE_STRATEGIES[strategy]
            signals = fn(closes, **params)
            if sentiment_filter:  # B6: лонг только при неотрицательном новостном фоне
                gate = sentiment_gate(dates, _article_scores(session, asset.id))
                signals = combine_and(signals, gate)
        else:
            raise ValueError(
                f"Неизвестная стратегия: {strategy}. "
                f"Доступно: {sorted(PRICE_STRATEGIES) + sorted(OHLC_STRATEGIES) + ['sentiment']}"
            )
        result = run(closes, signals, cost_bps=cost_bps)
        # B4: alpha к индексу IMOEX за тот же период (если бенчмарк загружен).
        if dates:
            idx_ret = _benchmark_return_pct(session, dates[0], dates[-1])
            if idx_ret is not None:
                result.index_return_pct = idx_ret
                result.alpha_pct = round(result.total_return_pct - idx_ret, 2)
        return result


def walk_forward_asset(ticker: str, strategy: str = "sma_cross", *,
                       train: int = 120, test: int = 40,
                       objective: str = "sharpe",
                       grid: dict[str, list] | None = None,
                       cost_bps: float | None = None,
                       anchored: bool = False) -> WalkForwardResult | None:
    """Walk-forward анализ стратегии по истории актива из БД. None — нет актива.

    Подбирает параметры на in-sample окнах и снимает честную out-of-sample
    доходность. `grid` по умолчанию берётся из `DEFAULT_GRIDS` для стратегии;
    `cost_bps` None → значение из настроек. Только ценовые стратегии (sentiment
    не параметризуется сеткой). `train`/`test` — длины окон в барах.
    """
    from sqlalchemy import select

    from config.settings import get_settings
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset

    if cost_bps is None:
        cost_bps = get_settings().backtest_cost_bps
    if grid is None:
        grid = DEFAULT_GRIDS.get(strategy, {})
    valid = GRID_VALIDATORS.get(strategy)
    with session_scope() as session:
        asset = session.scalars(
            select(Asset).where(Asset.ticker == ticker.upper())
        ).first()
        if asset is None:
            return None
        dates, closes = _price_series_with_dates(session, asset.id)
        strategy_arg: str | Callable[..., list[int]] = strategy
        if strategy in OHLC_STRATEGIES:  # свечные паттерны: подставляем OHLC
            strategy_arg = _ohlc_strategy_fn(strategy, *_ohlc_series(session, asset.id))
        result = walk_forward(
            closes, strategy_arg, grid, train=train, test=test, objective=objective,
            cost_bps=cost_bps, anchored=anchored, valid=valid,
        )
        # B4: alpha честной OOS-доходности к индексу IMOEX на покрытом OOS-отрезке.
        if result.folds:
            first_ts = result.folds[0].test_start
            last_te = result.folds[-1].test_end
            idx_ret = _benchmark_return_pct(session, dates[first_ts], dates[last_te - 1])
            if idx_ret is not None:
                result.oos_index_pct = idx_ret
                result.oos_alpha_pct = round(result.oos_return_pct - idx_ret, 2)
        return result
