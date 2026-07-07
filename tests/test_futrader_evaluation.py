"""Трек 2 / Фаза B: тесты строгой оценки — метрики, walk-forward сплиты, deflated Sharpe."""

from __future__ import annotations

import math

from geoanalytics.futrader.evaluation import (
    Fold,
    brier_score,
    calibration_gap,
    deflated_sharpe,
    max_drawdown,
    probability_of_backtest_overfitting,
    profit_factor,
    purged_kfold_splits,
    sharpe,
    sortino,
    walk_forward_splits,
)


class TestCalibration:
    def test_brier_perfect_predictions(self):
        assert brier_score([1, 0, 1], [1.0, 0.0, 1.0]) == 0.0

    def test_brier_worst_predictions(self):
        assert brier_score([1, 0], [0.0, 1.0]) == 1.0

    def test_brier_uncertain(self):
        assert brier_score([1, 0], [0.5, 0.5]) == 0.25

    def test_brier_empty_or_mismatched(self):
        assert brier_score([], []) is None
        assert brier_score([1], [0.5, 0.5]) is None

    def test_calib_gap_zero_when_mean_matches_rate(self):
        # средняя P = 0.5, win-rate = 0.5 → нет систематического сдвига
        assert calibration_gap([1, 0, 1, 0], [0.4, 0.6, 0.5, 0.5]) == 0.0

    def test_calib_gap_detects_overconfidence(self):
        # модель в среднем обещает 0.9, а выигрывает лишь половина → gap 0.4
        assert calibration_gap([1, 0, 1, 0], [0.9, 0.9, 0.9, 0.9]) == 0.4

    def test_calib_gap_empty_or_mismatched(self):
        assert calibration_gap([], []) is None
        assert calibration_gap([1, 0], [0.5]) is None


class TestMetrics:
    def test_sharpe_basic_and_annualize(self):
        rets = [0.01, 0.02, -0.005, 0.015, 0.0]
        sr = sharpe(rets)
        assert sr is not None and sr > 0
        ann = sharpe(rets, periods_per_year=252)
        assert math.isclose(ann, sr * math.sqrt(252), rel_tol=1e-9)

    def test_sharpe_zero_variance_none(self):
        assert sharpe([0.01, 0.01, 0.01]) is None

    def test_sharpe_too_short(self):
        assert sharpe([0.01]) is None

    def test_sortino_uses_downside_only(self):
        rets = [0.02, 0.03, -0.01, 0.04]
        so = sortino(rets)
        assert so is not None and so > 0

    def test_sortino_no_downside_none(self):
        assert sortino([0.01, 0.02, 0.03]) is None

    def test_max_drawdown(self):
        eq = [100, 120, 90, 110, 80, 130]
        # пик 120 → дно 80 = (120-80)/120 = 0.3333
        assert math.isclose(max_drawdown(eq), 40 / 120, rel_tol=1e-9)

    def test_max_drawdown_monotonic_zero(self):
        assert max_drawdown([100, 101, 102]) == 0.0

    def test_profit_factor(self):
        assert math.isclose(profit_factor([10, -5, 20, -5]), 30 / 10, rel_tol=1e-9)

    def test_profit_factor_no_losses_none(self):
        assert profit_factor([10, 5]) is None


class TestDeflatedSharpe:
    def test_high_sr_many_obs_high_confidence(self):
        dsr = deflated_sharpe(0.5, n_trials=1, n_obs=200)
        assert dsr is not None and dsr > 0.9

    def test_more_trials_lowers_confidence(self):
        few = deflated_sharpe(0.3, n_trials=1, n_obs=200)
        many = deflated_sharpe(0.3, n_trials=50, n_obs=200)
        assert few > many          # мультитестинг штрафует

    def test_too_few_obs_none(self):
        assert deflated_sharpe(0.5, n_trials=1, n_obs=1) is None


class TestWalkForward:
    def test_basic_expanding_splits(self):
        folds = walk_forward_splits(130, n_splits=5, min_train=30)
        assert len(folds) == 5
        assert all(isinstance(f, Fold) for f in folds)
        # train всегда с 0; тест-блоки идут подряд, последний добирает до n.
        assert folds[0].train_lo == 0
        assert folds[0].test_lo == 30
        assert folds[-1].test_hi == 130
        for f in folds:
            assert f.train_hi <= f.test_lo
            assert f.test_lo < f.test_hi

    def test_embargo_shrinks_train(self):
        # эмбарго укорачивает train на тот же тест-блок (раннюю долю может и вовсе отбросить).
        base = {f.test_lo: f for f in walk_forward_splits(130, n_splits=5, min_train=30, embargo=0)}
        emb = {f.test_lo: f for f in walk_forward_splits(130, n_splits=5, min_train=30, embargo=5)}
        common = set(base) & set(emb)
        assert common
        for tl in common:
            assert emb[tl].train_hi == base[tl].train_hi - 5

    def test_too_few_points_empty(self):
        assert walk_forward_splits(20, n_splits=5, min_train=30) == []

    def test_contiguous_test_blocks_cover_tail(self):
        folds = walk_forward_splits(100, n_splits=4, min_train=20)
        for a, b in zip(folds, folds[1:], strict=False):
            assert b.test_lo == a.test_hi      # без зазоров между тест-блоками


class TestPurgedKFold:
    def test_basic_folds_and_purge(self):
        # 12 меток, каждая живёт 1 единицу; 3 блока. Соседние с тест-окном выкидываются (purge).
        starts = list(range(12))
        ends = [s + 1 for s in starts]
        folds = purged_kfold_splits(starts, ends, n_splits=3)
        assert len(folds) == 3
        tr0, te0 = folds[0]
        assert te0 == [0, 1, 2, 3]                 # первый тест-блок
        assert 4 not in tr0                         # метка 4 пересекает окно теста → выкинута
        assert min(tr0) >= 5

    def test_embargo_extends_purge(self):
        starts = list(range(12))
        ends = [s + 1 for s in starts]
        tr_no = purged_kfold_splits(starts, ends, n_splits=3, embargo=0)[0][0]
        tr_emb = purged_kfold_splits(starts, ends, n_splits=3, embargo=2)[0][0]
        assert min(tr_emb) > min(tr_no)            # эмбарго отрезает ещё ближние наблюдения

    def test_unlabeled_excluded(self):
        starts = [0, 1, 2, 3, 4, 5]
        ends = [1, None, 3, None, 5, 6]            # None — неразмеченные
        folds = purged_kfold_splits(starts, ends, n_splits=2)
        for tr, te in folds:
            assert all(ends[i] is not None for i in te)
            assert all(ends[i] is not None for i in tr)

    def test_too_few_empty(self):
        assert purged_kfold_splits([0, 1], [1, 2], n_splits=5) == []


class TestPBO:
    def test_zero_when_is_best_is_oos_best(self):
        # лучший по IS конфиг всегда лучший по OOS → отбор надёжен → PBO=0.
        is_perf = [[2.0, 1.0]] * 4
        oos_perf = [[2.0, 1.0]] * 4
        assert probability_of_backtest_overfitting(is_perf, oos_perf) == 0.0

    def test_one_when_is_best_is_oos_worst(self):
        # лучший по IS конфиг всегда худший по OOS → чистое переобучение → PBO=1.
        is_perf = [[2.0, 1.0]] * 4
        oos_perf = [[1.0, 2.0]] * 4
        assert probability_of_backtest_overfitting(is_perf, oos_perf) == 1.0

    def test_none_when_empty(self):
        assert probability_of_backtest_overfitting([], []) is None
