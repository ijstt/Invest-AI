"""Трек 2 / Фаза B: строгая оценка — walk-forward, торговые метрики, deflated Sharpe.

Чтобы результатам ВЕРИТЬ (а не переобучаться на одном хвосте), оцениваем политику по нескольким
последовательным out-of-sample окнам (walk-forward с эмбарго против утечки времени) и считаем
метрики сверх win-rate: Sharpe/Sortino/maxDD/profit-factor. Так как мы перебираем много стратегий
и порогов (мультитестинг), «голый» Sharpe смещён вверх — корректируем deflated Sharpe ratio
(López de Prado): какова вероятность, что наблюдаемый Sharpe выше лучшего из N бесполезных проб.

Это чистое ядро (без БД/sklearn): на вход — доходности/эквити/индексы. DB-раннер и реестр моделей
надстраиваются сверху (`run_walk_forward`, `futures_model_runs`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from geoanalytics.core.logging import get_logger

log = get_logger("futrader.evaluation")


def sharpe(returns: list[float], *, periods_per_year: int | None = None) -> float | None:
    """Sharpe по ряду доходностей (на сделку/бар); аннуализация при заданном `periods_per_year`."""
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    sd = var ** 0.5
    if sd == 0:
        return None
    sr = mean / sd
    return sr * math.sqrt(periods_per_year) if periods_per_year else sr


def sortino(returns: list[float], *, periods_per_year: int | None = None) -> float | None:
    """Sortino: средняя доходность к ст. отклонению ТОЛЬКО отрицательных исходов (downside risk)."""
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    downside = [r for r in returns if r < 0]
    if not downside:
        return None
    dd = (sum(r ** 2 for r in downside) / len(downside)) ** 0.5
    if dd == 0:
        return None
    sr = mean / dd
    return sr * math.sqrt(periods_per_year) if periods_per_year else sr


def max_drawdown(equity: list[float]) -> float:
    """Максимальная просадка эквити-кривой (доля, ≥0): глубочайший спад от пика."""
    if not equity:
        return 0.0
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return mdd


def profit_factor(pnls: list[float]) -> float | None:
    """Profit-factor: сумма прибылей / |сумма убытков|. None без убытков (или пусто)."""
    if not pnls:
        return None
    gains = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    if losses == 0:
        return None
    return gains / losses


def brier_score(y: list[int], p: list[float]) -> float | None:
    """Brier-score = средний квадрат ошибки прогноза вероятности (ниже=лучше, ∈[0,1]).

    Прямая мера КАЧЕСТВА КАЛИБРОВКИ P(win): хорошо откалиброванная модель, чьи вероятности
    близки к исходам, даёт малый Brier. Контроль того, что калибровка (Пул 3) держится OOS.
    """
    if not y or len(y) != len(p):
        return None
    return sum((pi - yi) ** 2 for yi, pi in zip(y, p, strict=False)) / len(y)


def calibration_gap(y: list[int], p: list[float]) -> float | None:
    """Calibration-in-the-large: |средняя предсказанная P − фактический win-rate| (≥0, ниже=лучше).

    Устойчива на малой выборке (без биннинга, в отличие от ECE): ловит систематический сдвиг
    вероятностей. Большой gap = модель переоценивает/недооценивает шансы → калибровка поехала.
    """
    if not y or len(y) != len(p):
        return None
    return abs(sum(p) / len(p) - sum(y) / len(y))


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def deflated_sharpe(observed_sr: float, *, n_trials: int, n_obs: int,
                    skew: float = 0.0, kurtosis: float = 3.0) -> float | None:
    """Deflated Sharpe Ratio (López de Prado): P(истинный SR>0) с поправкой на мультитестинг.

    Поскольку перебрано `n_trials` стратегий, ожидаемый максимум Sharpe у бесполезных проб > 0.
    DSR = Φ( ((SR − SR0)·√(n_obs−1)) / √(1 − skew·SR + (kurt−1)/4·SR²) ), где SR0 — ожидаемый
    максимум при нулевом скилле. Возвращает ∈[0,1]; >0.95 — уверенно неслучайный эдж.
    SR здесь — НЕаннуализированный (на сделку). None при недостатке наблюдений.
    """
    if n_obs < 2 or n_trials < 1:
        return None
    # Ожидаемый максимум Sharpe среди n_trials независимых нулевых проб (E[max] ≈ через Gumbel).
    euler = 0.5772156649
    e_max = ((1 - euler) * _z(1 - 1.0 / n_trials)
             + euler * _z(1 - 1.0 / (n_trials * math.e)))
    sr0 = e_max / math.sqrt(n_obs - 1) if n_obs > 1 else 0.0
    denom = math.sqrt(max(1e-12, 1 - skew * observed_sr + (kurtosis - 1) / 4 * observed_sr ** 2))
    z = (observed_sr - sr0) * math.sqrt(n_obs - 1) / denom
    return _norm_cdf(z)


def _z(p: float) -> float:
    """Обратная функция стандартного нормального распределения (приближение Acklam)."""
    p = min(max(p, 1e-9), 1 - 1e-9)
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow = 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p <= 1 - plow:
        q = p - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
               (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
           ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)


@dataclass
class Fold:
    train_lo: int
    train_hi: int      # train = [train_lo, train_hi)
    test_lo: int
    test_hi: int       # test  = [test_lo, test_hi)


def walk_forward_splits(n: int, *, n_splits: int = 5, embargo: int = 0,
                        min_train: int = 30) -> list[Fold]:
    """Расширяющееся walk-forward разбиение `n` упорядоченных по времени точек.

    Хвост делится на `n_splits` равных тест-блоков; фолд k обучается на всём ДО блока (минус
    `embargo` точек против утечки соседних наблюдений), тестируется на блоке. Фолды с обучающей
    частью < `min_train` отбрасываются. Возвращает список `Fold` (полуоткрытые интервалы).
    """
    if n <= min_train or n_splits < 1:
        return []
    test_total = n - min_train
    block = test_total // n_splits
    if block <= 0:
        return []
    folds: list[Fold] = []
    for k in range(n_splits):
        test_lo = min_train + k * block
        test_hi = n if k == n_splits - 1 else min_train + (k + 1) * block
        train_hi = max(0, test_lo - embargo)
        if train_hi >= min_train:
            folds.append(Fold(0, train_hi, test_lo, test_hi))
    return folds


def purged_kfold_splits(starts: list, ends: list, *, n_splits: int = 5, embargo=0):
    """Purged K-fold (López de Prado): K смежных тест-блоков, из train выкинуты наблюдения, чья
    метка [start,end] ПЕРЕСЕКАЕТСЯ с тест-окном (+embargo) — убирает утечку перекрытых меток.

    `starts`/`ends` упорядочены по start (время входа/исхода; `ends[i]=None` — неразмеченное,
    не тестируется и не идёт в train). `embargo` — добавка к концу тест-окна (timedelta/число).
    Возвращает [(train_idx, test_idx)] — списки индексов.
    """
    n = len(starts)
    if n < n_splits or n_splits < 2:
        return []
    block = n // n_splits
    folds = []
    for k in range(n_splits):
        lo = k * block
        hi = n if k == n_splits - 1 else (k + 1) * block
        test_idx = [i for i in range(lo, hi) if ends[i] is not None]
        if not test_idx:
            continue
        t0 = starts[test_idx[0]]
        t1 = max(ends[i] for i in test_idx)
        t1e = t1 + embargo if embargo else t1
        train_idx = [j for j in range(n)
                     if not (lo <= j < hi) and ends[j] is not None
                     and (ends[j] < t0 or starts[j] > t1e)]
        if train_idx:
            folds.append((train_idx, test_idx))
    return folds


def probability_of_backtest_overfitting(is_perf: list[list[float]],
                                        oos_perf: list[list[float]]) -> float | None:
    """PBO (CSCV, López de Prado): доля сплитов, где ЛУЧШИЙ по in-sample конфиг оказывается ниже
    медианы out-of-sample. `is_perf`/`oos_perf` — матрицы [сплит][конфиг] метрики (больше=лучше).

    PBO→0 — выбор по IS надёжно переносится на OOS (эдж реален); PBO→1 — отбор ловит шум
    (переобучение). >0.5 — выбор лучшей стратегии по бэктесту не лучше монетки.
    """
    below = total = 0
    for is_row, oos_row in zip(is_perf, oos_perf, strict=False):
        if len(is_row) < 2 or len(oos_row) != len(is_row):
            continue
        best = max(range(len(is_row)), key=lambda c: is_row[c])
        rank = sum(1 for v in oos_row if v <= oos_row[best]) / len(oos_row)  # доля ≤ лучшего (0,1]
        if rank <= 0.5:
            below += 1
        total += 1
    return below / total if total else None


@dataclass
class WalkForwardResult:
    source: str
    asset_code: str | None
    interval: str
    threshold: float = 0.55
    n_samples: int = 0
    n_folds: int = 0
    n_taken: int = 0
    base_win_rate: float | None = None
    model_win_rate: float | None = None
    lift: float | None = None
    auc: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    max_drawdown: float | None = None
    profit_factor: float | None = None
    deflated_sharpe: float | None = None
    brier: float | None = None
    calib_gap: float | None = None
    n_trials: int = 1
    note: str = ""

    def as_row(self) -> dict:
        return {k: getattr(self, k) for k in (
            "source", "asset_code", "interval", "threshold", "n_folds", "n_samples", "n_taken",
            "base_win_rate", "model_win_rate", "lift", "auc", "sharpe", "sortino",
            "max_drawdown", "profit_factor", "deflated_sharpe", "n_trials", "note")}


def run_walk_forward(session, *, source: str = "sma_cross", asset_code: str | None = None,
                     interval: str = "1h", threshold: float = 0.55, n_splits: int = 5,
                     embargo: int = 2, min_train: int = 40, n_trials: int = 1,
                     start_cash: float = 100_000.0) -> WalkForwardResult:
    """Walk-forward оценка мета-фильтра по размеченным решениям (пулинг при asset_code=None).

    Для каждого фолда: обучение GBM на прошлом, гейт на тест-блоке при P≥threshold, сбор исходов
    взятых сделок (доходность в сторону ставки, P&L ₽). Метрики OOS-агрегированы по всем фолдам:
    lift над базой, AUC, Sharpe/Sortino/maxDD/profit-factor, deflated Sharpe (поправка на n_trials).
    """
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score

    from geoanalytics.futrader.policy import sanitize_fit_matrix, vectorize
    from geoanalytics.futrader.weights import uniqueness_weights
    from geoanalytics.storage.repositories import FuturesDecisionRepository

    res = WalkForwardResult(source=source, asset_code=asset_code, interval=interval,
                            threshold=threshold, n_trials=n_trials)
    rows = [r for r in FuturesDecisionRepository(session).labeled(
        asset_code=asset_code, source=source) if r.interval == interval]
    rows.sort(key=lambda r: r.ts)
    res.n_samples = len(rows)
    folds = walk_forward_splits(len(rows), n_splits=n_splits, embargo=embargo, min_train=min_train)
    if not folds:
        res.note = f"мало данных для walk-forward: {len(rows)} решений ({interval})"
        return res

    X = np.array([vectorize(r.features or {}, r.signed_qty) for r in rows], dtype=float)
    y = np.array([1 if r.label == "win" else 0 for r in rows], dtype=int)
    w = np.array(uniqueness_weights([(r.asset_code, r.ts, r.outcome_ts) for r in rows]),
                 dtype=float)
    sgn = np.array([1 if r.signed_qty >= 0 else -1 for r in rows], dtype=float)
    ret = np.array([r.outcome_return_pct or 0.0 for r in rows], dtype=float) * sgn
    pnl = np.array([r.outcome_pnl_rub or 0.0 for r in rows], dtype=float)

    taken_ret: list[float] = []
    taken_pnl: list[float] = []
    taken_y: list[int] = []
    test_y: list[int] = []
    test_p: list[float] = []
    base_wins = base_n = 0
    used_folds = 0
    for f in folds:
        ytr = y[f.train_lo:f.train_hi]
        if len(set(ytr.tolist())) < 2:
            continue
        used_folds += 1
        model = HistGradientBoostingClassifier(max_iter=200, max_depth=3, learning_rate=0.05,
                                               min_samples_leaf=5, l2_regularization=1.0,
                                               random_state=0)
        # Санитайз ПО ФОЛДУ + веса уникальности (Пул 4): перекрытые метки не считаются дважды.
        model.fit(sanitize_fit_matrix(X[f.train_lo:f.train_hi]), ytr,
                  sample_weight=w[f.train_lo:f.train_hi])
        proba = model.predict_proba(X[f.test_lo:f.test_hi])
        classes = list(model.classes_)
        wi = classes.index(1) if 1 in classes else proba.shape[1] - 1
        p = proba[:, wi]
        yte = y[f.test_lo:f.test_hi]
        base_wins += int(yte.sum())
        base_n += len(yte)
        test_y.extend(yte.tolist())
        test_p.extend(p.tolist())
        for j in np.where(p >= threshold)[0]:
            g = f.test_lo + int(j)
            taken_ret.append(float(ret[g]))
            taken_pnl.append(float(pnl[g]))
            taken_y.append(int(y[g]))

    res.n_folds = used_folds
    res.n_taken = len(taken_ret)
    res.base_win_rate = round(base_wins / base_n, 3) if base_n else None
    if taken_y:
        res.model_win_rate = round(sum(taken_y) / len(taken_y), 3)
        if res.base_win_rate is not None:
            res.lift = round(res.model_win_rate - res.base_win_rate, 3)
    if len(set(test_y)) == 2:
        try:
            res.auc = round(float(roc_auc_score(test_y, test_p)), 3)
        except ValueError:
            pass
    if test_y:
        # Калибровка P(win) OOS (Пул 7): держится ли калибровка Пула 3 вне обучения. Brier и
        # сдвиг calibration-in-the-large — наблюдатели «доказанности» + гейт промоушена ниже.
        bs = brier_score(test_y, test_p)
        res.brier = round(bs, 4) if bs is not None else None
        cg = calibration_gap(test_y, test_p)
        res.calib_gap = round(cg, 4) if cg is not None else None
        if cg is not None and cg > 0.15:
            log.warning("calibration_drift", source=source, asset_code=asset_code,
                        interval=interval, calib_gap=res.calib_gap, brier=res.brier)
    if len(taken_pnl) >= 2:
        # Метрики на ЧИСТОМ P&L (cost-aware, Пул 3): Sharpe инвариантен к масштабу, поэтому
        # Sharpe(pnl) корректен и учитывает издержки, заложенные в outcome_pnl_rub.
        sr = sharpe(taken_pnl)
        res.sharpe = round(sr, 3) if sr is not None else None
        so = sortino(taken_pnl)
        res.sortino = round(so, 3) if so is not None else None
        pf = profit_factor(taken_pnl)
        res.profit_factor = round(pf, 3) if pf is not None else None
        equity, acc = [start_cash], start_cash
        for pp in taken_pnl:
            acc += pp
            equity.append(acc)
        res.max_drawdown = round(max_drawdown(equity), 4)
        if sr is not None:
            dsr = deflated_sharpe(sr, n_trials=n_trials, n_obs=len(taken_ret))
            res.deflated_sharpe = round(dsr, 4) if dsr is not None else None
    if not res.note:
        res.note = f"walk-forward {used_folds} фолдов, взято {res.n_taken}"
    return res


def evaluate_and_record(session, *, model_path: str | None = None,
                        champion_metric: str = "deflated_sharpe",
                        max_calib_gap: float = 0.2, **kwargs) -> WalkForwardResult:
    """Прогнать walk-forward и записать в реестр; пометить чемпионом, если бьёт текущего.

    Чемпион меняется только при положительном lift, улучшении `champion_metric` (по умолчанию
    deflated Sharpe) И приемлемой OOS-калибровке (calib_gap ≤ `max_calib_gap`) — консервативно,
    чтобы шум на малой выборке и сломанная калибровка (Пул 7) не «угоняли» чемпиона.
    """
    from geoanalytics.storage.repositories import FuturesModelRunRepository

    res = run_walk_forward(session, **kwargs)
    repo = FuturesModelRunRepository(session)
    row = res.as_row()
    row["model_path"] = model_path
    run_id = repo.add(row)
    challenger = getattr(res, champion_metric)
    well_calibrated = res.calib_gap is None or res.calib_gap <= max_calib_gap
    if (res.lift is not None and res.lift > 0 and challenger is not None
            and res.n_taken >= 5 and well_calibrated):
        champ = repo.champion(source=res.source, asset_code=res.asset_code, interval=res.interval)
        best = getattr(champ, champion_metric) if champ is not None else None
        if best is None or challenger > best:
            repo.mark_champion(run_id, source=res.source, asset_code=res.asset_code,
                               interval=res.interval)
    return res


@dataclass
class PboResult:
    pbo: float | None = None
    n_folds: int = 0
    n_configs: int = 0
    configs: tuple = ()
    oos_sharpe_mean: dict = field(default_factory=dict)
    note: str = ""


def run_cpcv_pbo(session, *, sources, interval: str = "1h", n_splits: int = 6, embargo=0,
                 threshold: float = 0.55, min_train: int = 60) -> PboResult:
    """PBO по набору стратегий (Пул 4): на каждой — purged K-fold, метрика = Sharpe ЧИСТОГО P&L
    взятых мета-фильтром сделок (IS и OOS). Затем CSCV-PBO по конфигам-стратегиям (выровнены по
    индексу фолда). PBO→0 — отбор стратегии по бэктесту надёжен; >0.5 — ловим шум.
    """
    import numpy as np

    from geoanalytics.futrader.policy import _new_gbm, sanitize_fit_matrix, vectorize
    from geoanalytics.futrader.weights import uniqueness_weights
    from geoanalytics.storage.repositories import FuturesDecisionRepository

    repo = FuturesDecisionRepository(session)
    is_by: dict[str, dict[int, float]] = {}
    oos_by: dict[str, dict[int, float]] = {}
    for src in sources:
        rows = [r for r in repo.labeled(source=src) if r.interval == interval]
        rows.sort(key=lambda r: r.ts)
        if len(rows) < min_train + n_splits:
            continue
        X = np.array([vectorize(r.features or {}, r.signed_qty) for r in rows], dtype=float)
        y = np.array([1 if r.label == "win" else 0 for r in rows], dtype=int)
        w = np.array(uniqueness_weights([(r.asset_code, r.ts, r.outcome_ts) for r in rows]),
                     dtype=float)
        pnl = np.array([r.outcome_pnl_rub or 0.0 for r in rows], dtype=float)
        folds = purged_kfold_splits([r.ts for r in rows], [r.outcome_ts for r in rows],
                                    n_splits=n_splits, embargo=embargo)
        is_by[src], oos_by[src] = {}, {}
        for k, (tr, te) in enumerate(folds):
            tr = np.array(tr)
            te = np.array(te)
            if len(set(y[tr].tolist())) < 2:
                continue
            model = _new_gbm()
            model.fit(sanitize_fit_matrix(X[tr]), y[tr], sample_weight=w[tr])

            def _sharpe_taken(idx, m=model, X=X, pnl=pnl):
                proba = m.predict_proba(X[idx])
                classes = list(m.classes_)
                wi = classes.index(1) if 1 in classes else proba.shape[1] - 1
                taken = [float(pnl[idx[j]]) for j in range(len(idx))
                         if proba[j, wi] >= threshold]
                return sharpe(taken)

            is_s, oos_s = _sharpe_taken(tr), _sharpe_taken(te)
            if is_s is not None and oos_s is not None:
                is_by[src][k] = is_s
                oos_by[src][k] = oos_s

    configs = [s for s in sources if is_by.get(s)]
    if len(configs) < 2:
        return PboResult(n_configs=len(configs),
                         note="нужно ≥2 стратегии с фолдами для PBO (накопите данные)")
    common = sorted(set.intersection(*[set(is_by[s]) for s in configs]))
    is_perf = [[is_by[s][k] for s in configs] for k in common]
    oos_perf = [[oos_by[s][k] for s in configs] for k in common]
    pbo = probability_of_backtest_overfitting(is_perf, oos_perf)
    oos_mean = {s: round(sum(oos_by[s].values()) / len(oos_by[s]), 3) for s in configs}
    return PboResult(pbo=round(pbo, 3) if pbo is not None else None, n_folds=len(common),
                     n_configs=len(configs), configs=tuple(configs), oos_sharpe_mean=oos_mean,
                     note=f"PBO по {len(configs)} стратегиям, {len(common)} общих фолдов")
