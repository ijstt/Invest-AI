"""Трек 2 / T2.4: обучаемая политика — табличный мета-фильтр над правилами.

Концепция (выбор пользователя): правило (sma_cross/momentum/…) предлагает сделку, обучаемая модель
оценивает `P(win)` по контекст-признакам решения и ГЕЙТИТ (брать ли) + САЙЗИТ (сколько контрактов).
Это и есть «учится на своих исходах»: модель тренируется на размеченных решениях `futures_decisions`
(T2.3). Модель — `HistGradientBoostingClassifier` (нативно ест NaN от прогрева признаков, CPU,
интерпретируемо), НЕ языковая (форма данных — табличная числовая; продакшн-LLM не трогаем).

Честность: финданные НЕ перемешиваем — оценка на time-ordered hold-out (хвост по времени).
Выборка сейчас крошечная (десятки решений) — инфраструктура готова, реальный эдж придёт по мере
накопления решений; метрики печатаются честно (база vs модель), ниже порога — отказ от обучения.
Петля самообучения: накопили решения (T2.3) → train_policy → политика гейтит будущие сделки →
дозрело больше → переобучили. T2.5 повесит на это gated автоисполнение.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

# Канонический порядок признаков (последний — направление ставки). Пропуски → NaN (модель ест).
# Фаза A добавила признаки-эдж Трека 1 (режим/сентимент/кросс-актив) и код инструмента (пулинг) —
# старые решения без этих ключей дают NaN (обратносовместимо: GBM нативно ест пропуски).
FEATURE_ORDER = ("ret_1", "ret_5", "ret_20", "rsi_14", "vol_20", "sma_gap_20", "macd_hist",
                 "range_pos", "regime_state", "regime_vol", "sent_ewma", "sent_breadth",
                 "brent_ret", "usd_ret", "imoex_ret", "vol_z", "term_slope", "hour",
                 "cdl_wick", "cdl_engulf", "asset_sent_ewma", "asset_sent_breadth",
                 "instr", "dir")

MODEL_DIR = Path("data/futrader")


def vectorize(features: dict, signed_qty: int) -> list[float]:
    """Признаки решения + знак направления → фиксированный числовой вектор (пропуски = NaN)."""
    vec = [float(features.get(k, math.nan)) for k in FEATURE_ORDER[:-1]]
    vec.append(float(np.sign(signed_qty)))
    return vec


def sanitize_fit_matrix(X):
    """Заполнить ПОЛНОСТЬЮ пустые (all-NaN) столбцы нулём перед обучением.

    HistGradientBoosting нативно ест пропуски, НО падает на столбце без единого значения (нечего
    бинировать). Такой признак отсутствует в подвыборке целиком (напр. сентимент до начала его
    истории) — заменяем константой 0: дерево по нему не сплитит, на инференсе NaN там безвреден.
    """
    X = np.asarray(X, dtype=float)
    if X.ndim == 2 and X.shape[0] > 0:
        all_nan = np.all(np.isnan(X), axis=0)
        if all_nan.any():
            X = X.copy()
            X[:, all_nan] = 0.0
    return X


@dataclass
class TrainResult:
    source: str
    asset_code: str | None
    n_total: int = 0
    n_train: int = 0
    n_test: int = 0
    base_win_rate: float | None = None      # доля win в тесте (что даёт «брать всё»)
    model_precision: float | None = None    # доля win среди взятых моделью (P≥порог) в тесте
    lift: float | None = None               # model_precision − base_win_rate
    auc: float | None = None
    n_taken: int = 0                         # сколько сделок модель взяла бы в тесте
    threshold: float = 0.55
    trained: bool = False
    calibrated: bool = False                 # применена ли калибровка вероятностей (Пул 3)
    note: str = ""
    model_path: str | None = None


@dataclass
class LearnedPolicy:
    """Загруженная обученная политика: score(P(win)) + decide(гейт+сайз сигнала правила)."""

    model: object
    meta: dict = field(default_factory=dict)

    def score(self, features: dict, signed_qty: int) -> float:
        """Вероятность win для решения с такими признаками и направлением."""
        x = np.array([vectorize(features, signed_qty)], dtype=float)
        proba = self.model.predict_proba(x)[0]
        classes = list(getattr(self.model, "classes_", [0, 1]))
        idx = classes.index(1) if 1 in classes else len(proba) - 1
        return float(proba[idx])

    def decide(self, signal_dir: int, features: dict, *, threshold: float = 0.55,
               max_qty: int = 3) -> int:
        """Сигнал правила (signal_dir ∈ {-1,0,1}) → знаковое кол-во контрактов (0 = пропуск).

        Ниже порога P(win) — отказ от сделки; иначе размер растёт с уверенностью до `max_qty`.
        """
        if signal_dir == 0:
            return 0
        p = self.score(features, signal_dir)
        if p < threshold:
            return 0
        span = max(1e-9, 1.0 - threshold)
        qty = 1 + round((p - threshold) / span * (max_qty - 1))
        return signal_dir * max(1, min(qty, max_qty))


def _model_path(asset_code: str | None, source: str) -> Path:
    tag = f"{asset_code or 'all'}_{source}"
    return MODEL_DIR / f"policy_{tag}.joblib"


def _new_gbm():
    from sklearn.ensemble import HistGradientBoostingClassifier

    return HistGradientBoostingClassifier(max_iter=200, max_depth=3, learning_rate=0.05,
                                          min_samples_leaf=5, l2_regularization=1.0,
                                          random_state=0)


def _fit_calibrated(X_tr, y_tr, w_tr=None, *, calib_frac: float = 0.25, min_calib: int = 60):
    """Обучить GBM (с весами уникальности) + калибровать P(win) на хвосте train (Пул 3+4).

    Калибровка делает P(win) НАСТОЯЩЕЙ вероятностью (порог гейта осмыслен). Калибруем на отдельном
    ВРЕМЕННОМ хвосте train (не перемешивая): GBM учится на голове, изотоника/сигмоида — на хвосте.
    Возвращает (модель с `predict_proba`, был_ли_калиброван). При нехватке данных — просто GBM.
    """
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.frozen import FrozenEstimator

    n = len(X_tr)
    csplit = int(n * (1.0 - calib_frac))
    X_fit, X_cal = X_tr[:csplit], X_tr[csplit:]
    y_fit, y_cal = y_tr[:csplit], y_tr[csplit:]
    w_fit = w_tr[:csplit] if w_tr is not None else None

    base = _new_gbm()
    # Санитайз обучающего среза: признак, отсутствующий в нём целиком, валит HGB-биннинг.
    base.fit(sanitize_fit_matrix(X_fit), y_fit, sample_weight=w_fit)
    if len(X_cal) >= min_calib and len(set(y_cal.tolist())) == 2:
        method = "isotonic" if len(X_cal) >= 300 else "sigmoid"
        try:
            cal = CalibratedClassifierCV(FrozenEstimator(base), method=method)
            cal.fit(X_cal, y_cal)
            return cal, True
        except Exception:  # noqa: BLE001 — при сбое калибровки откатываемся на сырой GBM
            pass
    # мало данных под калибровку — учим GBM на всём train (с весами) без калибровки.
    base = _new_gbm()
    base.fit(sanitize_fit_matrix(X_tr), y_tr, sample_weight=w_tr)
    return base, False


def train_policy(session, *, source: str = "sma_cross", asset_code: str | None = None,
                 threshold: float = 0.55, min_samples: int = 30,
                 test_frac: float = 0.3) -> TrainResult:
    """Обучить мета-фильтр на размеченных решениях (time-ordered hold-out). Сохранить модель.

    `asset_code=None` — учить на всех активах данной политики. Возвращает честные метрики
    (база vs модель на хвосте по времени). Ниже `min_samples` или при вырожденных классах —
    не обучает (note объясняет).
    """
    import joblib
    from sklearn.metrics import roc_auc_score

    from geoanalytics.futrader.weights import uniqueness_weights
    from geoanalytics.storage.repositories import FuturesDecisionRepository

    rows = FuturesDecisionRepository(session).labeled(asset_code=asset_code, source=source)
    res = TrainResult(source=source, asset_code=asset_code, n_total=len(rows), threshold=threshold)
    if len(rows) < min_samples:
        res.note = (f"мало данных: {len(rows)} < {min_samples} размеченных решений — "
                    f"инфраструктура готова, накопите больше (geo futures-intraday log-decisions)")
        return res

    # time-ordered: rows уже по ts ↑. Хвост — hold-out (без перемешивания финданных).
    rows.sort(key=lambda r: r.ts)
    X = np.array([vectorize(r.features or {}, r.signed_qty) for r in rows], dtype=float)
    y = np.array([1 if r.label == "win" else 0 for r in rows], dtype=int)
    w = np.array(uniqueness_weights([(r.asset_code, r.ts, r.outcome_ts) for r in rows]),
                 dtype=float)
    split = int(len(rows) * (1.0 - test_frac))
    X_tr, X_te, y_tr, y_te = X[:split], X[split:], y[:split], y[split:]
    w_tr = w[:split]
    res.n_train, res.n_test = len(X_tr), len(X_te)
    if len(set(y_tr)) < 2:
        res.note = "в обучающей части один класс — модель не информативна; нужно разнообразие"
        return res

    # Калиброванная (Пул 3) и взвешенная по уникальности (Пул 4) модель.
    model, res.calibrated = _fit_calibrated(X_tr, y_tr, w_tr)

    res.base_win_rate = round(float(y_te.mean()), 3) if len(y_te) else None
    if len(y_te):
        proba = model.predict_proba(X_te)
        classes = list(model.classes_)
        win_idx = classes.index(1) if 1 in classes else proba.shape[1] - 1
        p_win = proba[:, win_idx]
        taken = p_win >= threshold
        res.n_taken = int(taken.sum())
        if res.n_taken:
            res.model_precision = round(float(y_te[taken].mean()), 3)
            if res.base_win_rate is not None:
                res.lift = round(res.model_precision - res.base_win_rate, 3)
        if len(set(y_te)) == 2:
            try:
                res.auc = round(float(roc_auc_score(y_te, p_win)), 3)
            except ValueError:
                res.auc = None

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    path = _model_path(asset_code, source)
    meta = {"source": source, "asset_code": asset_code, "threshold": threshold,
            "trained_at": datetime.utcnow().isoformat(), "n_train": res.n_train,
            "calibrated": res.calibrated, "features": list(FEATURE_ORDER)}
    joblib.dump({"model": model, "meta": meta}, path)
    path.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    res.trained = True
    res.model_path = str(path)
    return res


def load_policy(asset_code: str | None, source: str) -> LearnedPolicy | None:
    """Загрузить сохранённую политику (None — не обучена). Фолбэк на all-модель при отсутствии."""
    import joblib

    for path in (_model_path(asset_code, source), _model_path(None, source)):
        if path.exists():
            blob = joblib.load(path)
            meta = blob.get("meta", {})
            # Защита от рассинхрона набора признаков: модель, обученная на СТАРОМ FEATURE_ORDER
            # (напр. до добавления свечных паттернов), несовместима с новым serve-вектором —
            # игнорируем как протухшую (петля переобучит её этим же циклом), чтобы не падать на
            # несоответствии числа фич. Старые модели без ключа `features` — тоже считаем стейл.
            if list(meta.get("features", ())) != list(FEATURE_ORDER):
                continue
            return LearnedPolicy(model=blob["model"], meta=meta)
    return None


def _run_segment(spec, bars, closes, highs, lows, signals, *, gate=None, cash: float,
                 threshold: float, max_qty: int, sizing: str = "vol_target",
                 target_risk_pct: float = 1.0, max_dd_pct: float = 0.0):
    """Прогнать ExecutionSimulator по сегменту баров с лонг/выход по сигналу.

    `gate(features) -> P(win)` (опц.): на входе берём сделку лишь при P≥threshold. `sizing`:
    `vol_target` (Фаза C — vol-targeting × дробный Келли, риск-стабильный размер) или `linear`
    (наследие T2.4, размер ∝ P — для сравнения). `max_dd_pct>0` — circuit-breaker: при просадке
    сверх лимита новые входы блокируются. Индексы баров сегмента — глобальные (в `closes`/…).
    """
    from geoanalytics.futrader.decisions import extract_features
    from geoanalytics.futrader.execution import ExecutionSimulator, Order
    from geoanalytics.futrader.labeling import bar_return_std
    from geoanalytics.futrader.sizing import drawdown_breached, position_size

    sim = ExecutionSimulator(spec, starting_cash=cash, slippage_ticks=1)
    pos = 0
    equity_curve = [cash]
    for gi, bar in bars:
        target = signals[gi]
        if target == 1 and pos == 0 and not drawdown_breached(equity_curve, limit_pct=max_dd_pct):
            qty = 1
            ok = True
            if gate is not None:
                p = gate(extract_features(closes, highs, lows, gi), 1)
                if sizing == "vol_target":
                    vol = bar_return_std(closes, gi) or 0.0
                    qty = position_size(p, equity=sim._equity(bar.close), price=bar.close,
                                        vol_fraction=vol, spec=spec, threshold=threshold,
                                        target_risk_pct=target_risk_pct, max_qty=max_qty)
                    ok = qty > 0
                else:
                    ok = p >= threshold
                    if ok:
                        span = max(1e-9, 1.0 - threshold)
                        qty = max(1, min(1 + round((p - threshold) / span * (max_qty - 1)),
                                         max_qty))
            if ok:
                sim.submit(Order("buy", qty), bar.ts, price=bar.close)
                pos = 1
        elif target == 0 and pos == 1:
            held = sim.net_qty
            if held > 0:
                sim.submit(Order("sell", held), bar.ts, price=bar.close)
            pos = 0
        equity_curve.append(sim.mark(bar.ts, bar.close))
    return sim.finalize()


def evaluate_on_simulator(session, policy: LearnedPolicy, ticker: str, interval: str, *,
                          source: str = "sma_cross", threshold: float = 0.55,
                          test_frac: float = 0.3, cash: float = 100_000.0, max_qty: int = 3,
                          sizing: str = "vol_target", target_risk_pct: float = 1.0,
                          max_dd_pct: float = 25.0):
    """Честное сравнение out-of-sample: сырое правило vs фильтр политики на хвосте по времени.

    Гейт-путь использует Фаза-C сайзинг (`vol_target` по умолчанию) и circuit-breaker `max_dd_pct`.
    Возвращает (raw SimResult, gated SimResult) или None при нехватке данных/спеки.
    """
    from geoanalytics.analytics.history import _front_futures_secid
    from geoanalytics.futrader.continuous import continuous_series
    from geoanalytics.futrader.data import _asset_code_for, fetch_contract_spec
    from geoanalytics.futrader.decisions import SIGNAL_FNS

    secid = _front_futures_secid(_asset_code_for(ticker))
    spec = fetch_contract_spec(secid) if secid else None
    series = continuous_series(session, ticker, interval=interval)
    if spec is None or not series.bars:
        return None
    from geoanalytics.futrader.signals import apply_strategy
    bars = series.bars
    closes = [b.close for b in bars]
    opens = [getattr(b, "open", b.close) for b in bars]
    highs = [getattr(b, "high", b.close) for b in bars]
    lows = [getattr(b, "low", b.close) for b in bars]
    signals = apply_strategy(SIGNAL_FNS[source], source, closes,
                             opens=opens, highs=highs, lows=lows)
    start = int(len(bars) * (1.0 - test_frac))
    segment = list(enumerate(bars))[start:]   # (глобальный индекс, бар) на хвосте
    raw = _run_segment(spec, segment, closes, highs, lows, signals, gate=None,
                       cash=cash, threshold=threshold, max_qty=max_qty)
    gated = _run_segment(spec, segment, closes, highs, lows, signals, gate=policy.score,
                        cash=cash, threshold=threshold, max_qty=max_qty, sizing=sizing,
                        target_risk_pct=target_risk_pct, max_dd_pct=max_dd_pct)
    return raw, gated
