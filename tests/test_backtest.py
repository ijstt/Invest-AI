"""Тесты бэктеста M4: генераторы сигналов и ядро симуляции (чистые функции)."""

from __future__ import annotations

from datetime import date

from geoanalytics.analytics.backtest import (
    bollinger_signals,
    buy_hold_return_pct,
    combine_and,
    macd_cross_signals,
    make_folds,
    momentum_signals,
    optimize,
    param_grid,
    run,
    sentiment_gate,
    sentiment_signals,
    sma_cross_signals,
    walk_forward,
)


def test_sma_cross_flat_before_slow_window():
    """Пока не накоплено `slow` баров — сигнал 0 (нет данных для медленной SMA)."""
    closes = [float(i) for i in range(1, 11)]  # растущий ряд 1..10
    sig = sma_cross_signals(closes, fast=3, slow=5)
    assert len(sig) == len(closes)
    assert sig[:4] == [0, 0, 0, 0]      # первые 4 бара — недостаточно для slow=5
    assert sig[-1] == 1                  # на росте быстрая SMA выше медленной


def test_momentum_signals():
    closes = [10, 10, 10, 11, 9]  # вверх к индексу 3, вниз к индексу 4
    sig = momentum_signals(closes, lookback=2)
    assert sig == [0, 0, 0, 1, 0]


def test_sentiment_signals_decay():
    dates = [date(2026, 6, 1), date(2026, 6, 3), date(2026, 6, 10)]
    scored = [(date(2026, 6, 1), 0.5), (date(2026, 6, 2), -0.8)]
    # 1 июня: только позитив → 1; 3 июня: позитив+негатив = -0.3 → 0;
    # 10 июня: обе новости старше decay_days=5 → 0.
    assert sentiment_signals(dates, scored, decay_days=5) == [1, 0, 0]


def test_macd_cross_signals_length_and_uptrend():
    """Длина = длине ряда; на устойчивом росте в конце сигнал лонг (hist > 0)."""
    closes = [100 * 1.01 ** i for i in range(60)]
    sig = macd_cross_signals(closes)
    assert len(sig) == len(closes)
    assert sig[:30] == [0] * 30          # прогрев MACD — вне рынка
    assert sig[-1] == 1                   # ускоряющийся рост → лонг


def test_bollinger_signals_enters_on_dip_exits_at_mid():
    """Вход у нижней полосы (просадка), выход на возврате к средней."""
    closes = [100.0] * 20 + [80.0] + [100.0]  # резкая просадка ниже нижней полосы, затем возврат
    sig = bollinger_signals(closes, window=20, k=2.0)
    assert len(sig) == len(closes)
    assert sig[20] == 1                   # просадка → вход в лонг
    assert sig[-1] == 0                   # возврат к средней → выход


def test_run_all_long_matches_buy_hold():
    """Стратегия «всегда в лонге» воспроизводит доходность buy & hold."""
    closes = [100, 110, 121, 133.1]  # +10% каждый бар
    res = run(closes, [1, 1, 1, 1])
    assert res.total_return_pct == res.buy_hold_return_pct
    assert res.exposure == 0.75  # held = [0,1,1,1] → 3/4 (первый бар вне рынка)


def test_run_no_lookahead():
    """Сигнал на последнем баре не влияет на результат (исполнение со следующего бара)."""
    closes = [10, 11, 12, 13]
    res = run(closes, [0, 0, 0, 1])  # held = [0,0,0,0] → ничего не куплено
    assert res.total_return_pct == 0.0
    assert res.num_trades == 0


def test_run_extracts_trades_and_hit_rate():
    closes = [10, 11, 12, 11, 12, 13]
    res = run(closes, [1, 1, 0, 0, 1, 1])  # held = [0,1,1,0,0,1]
    assert res.num_trades == 2
    t1, t2 = res.trades
    assert (t1.entry_idx, t1.exit_idx, t1.entry_price, t1.exit_price) == (0, 2, 10, 12)
    assert (t2.entry_idx, t2.exit_idx) == (4, 5)
    assert res.hit_rate == 1.0  # обе сделки прибыльные


def test_run_drawdown_and_metrics():
    """Проседающий участок даёт ненулевую просадку; метрики заполнены."""
    closes = [100, 120, 90, 110]
    res = run(closes, [1, 1, 1, 1])  # held=[0,1,1,1]: ловим +20%, -25%, +22%
    assert res.max_drawdown_pct > 0
    assert res.sharpe is not None
    assert res.bars == 4
    assert len(res.equity_curve) == 4


def test_run_rejects_mismatched_signal_length():
    assert run([1, 2, 3], [1, 0]).bars == 3  # длины не совпали → пустой результат
    assert run([1, 2, 3], [1, 0]).total_return_pct == 0.0


def test_run_zero_cost_matches_gross():
    """Без издержек чистая доходность равна валовой (регресс к прежнему поведению)."""
    closes = [10, 11, 12, 11, 12, 13]
    res = run(closes, [1, 1, 0, 0, 1, 1], cost_bps=0.0)
    assert res.total_return_pct == res.total_return_gross_pct
    assert res.cost_bps == 0.0


def test_run_cost_charged_per_position_change():
    """Издержка списывается на смене позиции; на плоском ряду чистая = −издержка."""
    closes = [100, 100, 100, 100, 100]  # плоский ряд → валовая доходность 0
    # signals=[1,1,1,1,1] → held=[0,1,1,1,1]: единственная смена 0→1 на t=1 → одна сторона.
    res = run(closes, [1, 1, 1, 1, 1], cost_bps=10.0)  # 10 б.п. = 0.1% за сторону
    assert res.total_return_gross_pct == 0.0
    assert res.total_return_pct == -0.1  # один переход × 0.1%
    assert res.cost_bps == 10.0


def test_run_gross_ge_net_with_costs():
    """При положительных издержках валовая доходность ≥ чистой."""
    closes = [10, 11, 12, 11, 12, 13]
    res = run(closes, [1, 1, 0, 0, 1, 1], cost_bps=20.0)
    assert res.total_return_gross_pct >= res.total_return_pct


def test_run_calmar_equals_cagr_over_maxdd():
    """Кальмар = CAGR / макс. просадка (обе в %)."""
    closes = [100, 120, 90, 110]
    res = run(closes, [1, 1, 1, 1])
    assert res.cagr_pct is not None and res.max_drawdown_pct > 0
    assert res.calmar == round(res.cagr_pct / res.max_drawdown_pct, 2)


def test_run_sortino_ignores_upside():
    """Сортино определён при наличии просадочных баров; без них — None."""
    # Монотонный рост в лонге → нет отрицательных доходностей → downside=0 → sortino None.
    up = run([100, 110, 121, 133.1], [1, 1, 1, 1])
    assert up.sortino is None
    # Ряд с падением → есть downside → sortino посчитан.
    mixed = run([100, 120, 90, 110], [1, 1, 1, 1])
    assert mixed.sortino is not None


def test_run_profit_factor_and_avg_trades():
    """Profit-factor = сумма прибылей / |сумма убытков|; средние по сделкам."""
    # held=[0,1,1,0,1,1]: сделка1 10→12 (+20%), сделка2 12→13 (+8.33%) — обе прибыльные.
    closes = [10, 11, 12, 11, 12, 13]
    res = run(closes, [1, 1, 0, 0, 1, 1])
    assert res.num_trades == 2
    assert res.avg_win_pct is not None and res.avg_win_pct > 0
    assert res.avg_loss_pct is None          # убыточных сделок нет
    assert res.profit_factor is None         # |сумма убытков| = 0 → не определён


def test_run_profit_factor_with_losing_trade():
    """С убыточной и прибыльной сделкой profit-factor конечен и > 0."""
    # held=[0,1,0,1,0,0]: сделка1 10→8 (−20%, убыток), сделка2 10→15 (+50%, прибыль).
    closes = [10, 8, 10, 15, 15, 15]
    res = run(closes, [1, 0, 1, 0, 0, 0])
    assert res.num_trades == 2
    assert res.avg_loss_pct is not None and res.avg_loss_pct < 0
    assert res.profit_factor == round(50.0 / 20.0, 2)  # 2.5


# --------------------------------------------------------------------------- #
# B5: подбор параметров и walk-forward (out-of-sample).
# --------------------------------------------------------------------------- #
def test_param_grid_cartesian_product():
    grid = param_grid({"fast": [10, 20], "slow": [50, 100]})
    assert len(grid) == 4
    assert {"fast": 10, "slow": 50} in grid
    assert {"fast": 20, "slow": 100} in grid


def test_param_grid_empty_is_single_default():
    assert param_grid({}) == [{}]


def test_make_folds_rolling_non_overlapping_test():
    # n=10, train=4, test=2 → старты 0,2,4 (start+train+test<=10).
    folds = make_folds(10, train=4, test=2)
    assert folds == [(0, 4, 4, 6), (2, 6, 6, 8), (4, 8, 8, 10)]
    # test-окна идут подряд и не пересекаются.
    test_spans = [(ts, te) for _, _, ts, te in folds]
    assert test_spans == [(4, 6), (6, 8), (8, 10)]


def test_make_folds_anchored_train_from_zero():
    folds = make_folds(10, train=4, test=2, anchored=True)
    assert all(trs == 0 for trs, _, _, _ in folds)  # train всегда от начала
    assert folds[-1] == (0, 8, 8, 10)


def test_make_folds_insufficient_history_empty():
    assert make_folds(5, train=4, test=2) == []


def test_optimize_picks_best_by_objective():
    # Устойчивый рост: чем короче lookback, тем больше экспозиция/доходность.
    closes = [100 * 1.01 ** i for i in range(80)]
    res = optimize(closes, "momentum", {"lookback": [10, 20, 40]},
                   objective="total_return")
    assert res.best_params == {"lookback": 10}        # ранний вход → выше доходность
    assert res.objective == "total_return"
    assert len(res.leaderboard) == 3
    # Leaderboard отсортирован по убыванию score.
    scores = [s for _, s in res.leaderboard]
    assert scores == sorted(scores, reverse=True)


def test_optimize_valid_filters_invalid_combos():
    closes = [100.0 + i for i in range(60)]
    res = optimize(closes, "sma_cross", {"fast": [10, 50], "slow": [10, 50]},
                   objective="total_return", valid=lambda p: p["fast"] < p["slow"])
    # Из 4 комбинаций валидна только fast=10/slow=50.
    assert len(res.leaderboard) == 1
    assert res.best_params == {"fast": 10, "slow": 50}


def test_optimize_unknown_objective_raises():
    import pytest
    with pytest.raises(ValueError, match="цель"):
        optimize([1.0, 2.0, 3.0], "momentum", {"lookback": [2]}, objective="bogus")


def test_walk_forward_structure_and_no_lookahead_warmup():
    closes = [100 * 1.005 ** i for i in range(120)]
    res = walk_forward(closes, "momentum", {"lookback": [10, 20, 40]},
                       train=40, test=20, objective="total_return")
    assert res.strategy == "momentum"
    assert len(res.folds) > 0
    # Test-окна идут подряд (склейка OOS-кривой без дыр).
    for prev, cur in zip(res.folds, res.folds[1:], strict=False):  # парный обход
        assert cur.test_start == prev.test_end
    # OOS-кривая длиннее одного бара, доходность определена.
    assert len(res.oos_equity) > 1
    assert isinstance(res.oos_return_pct, float)


def test_walk_forward_efficiency_below_one_on_random_walk():
    """На псевдослучайном ряде подгонка не держится вне выборки → efficiency < 1."""
    import random
    rng = random.Random(42)
    price, closes = 100.0, []
    for _ in range(200):
        price *= 1 + rng.uniform(-0.02, 0.02)
        closes.append(price)
    res = walk_forward(closes, "sma_cross",
                       {"fast": [5, 10, 20], "slow": [30, 50, 100]},
                       train=80, test=30, objective="total_return",
                       valid=lambda p: p["fast"] < p["slow"])
    assert len(res.folds) >= 2
    # In-sample подбор оптимистичен; OOS честнее. На шуме разрыв виден.
    if res.efficiency is not None:
        assert res.efficiency < 1.0


def test_sentiment_gate_blocks_negative_background():
    """B6: фильтр пропускает неотрицательный фон, блокирует негативный."""
    dates = [date(2026, 6, 1), date(2026, 6, 3), date(2026, 6, 10)]
    scored = [(date(2026, 6, 1), 0.5), (date(2026, 6, 2), -0.8)]
    # 1 июня: +0.5 ≥ 0 → 1; 3 июня: -0.3 < 0 → 0; 10 июня: новости старше decay → 0 ≥ 0 → 1.
    assert sentiment_gate(dates, scored, decay_days=5) == [1, 0, 1]


def test_combine_and_is_elementwise_min():
    assert combine_and([1, 1, 0, 1], [1, 0, 0, 1]) == [1, 0, 0, 1]
    assert combine_and([1, 1, 1]) == [1, 1, 1]      # один ряд — он сам
    assert combine_and() == []                       # без рядов — пусто


def test_sentiment_filter_reduces_exposure_vs_raw():
    """Фильтр поверх ценового сигнала не увеличивает экспозицию (только режет лонги)."""
    closes = [100.0 + i for i in range(10)]
    price_sig = momentum_signals(closes, lookback=2)
    dates = [date(2026, 6, d) for d in range(1, 11)]
    neg = [(date(2026, 6, 5), -1.0)]  # негативный фон около 5 июня
    gate = sentiment_gate(dates, neg, decay_days=3)
    filtered = combine_and(price_sig, gate)
    assert all(f <= p for f, p in zip(filtered, price_sig, strict=True))


def test_buy_hold_return_pct():
    """B4: buy&hold-доходность ряда (база для alpha к индексу)."""
    assert buy_hold_return_pct([100.0, 110.0]) == 10.0
    assert buy_hold_return_pct([100.0, 90.0]) == -10.0
    assert buy_hold_return_pct([100.0]) is None        # мало данных
    assert buy_hold_return_pct([0.0, 100.0]) is None   # нулевая база


def test_walk_forward_empty_when_history_too_short():
    res = walk_forward([100.0, 101.0, 102.0], "momentum", {"lookback": [2]},
                       train=40, test=20)
    assert res.folds == []
    assert res.oos_return_pct == 0.0
    assert res.efficiency is None
