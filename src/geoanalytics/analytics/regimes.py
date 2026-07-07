"""G2 (Волна 3): режимы рынка — гауссовский HMM по волатильности IMOEX и USD/RUB.

Контекст «сейчас кризис» меняет интерпретацию всего: пороги алертов, выбор
стратегий бэктеста, вес новостей. Скрытые состояния HMM по [log EWMA-vol IMOEX,
log EWMA-vol USDRUB] естественно разделяются по уровню волатильности; states
упорядочиваются по vol → «спокойный» / «повышенный» / «кризис».

Реализация самодостаточна (numpy, EM + Viterbi, диагональные ковариации):
инициализация по квантилям vol — детерминированная, без случайных стартов,
результат воспроизводим. ~1250 дневных точек × 3 состояния — доли секунды.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
from sqlalchemy.orm import Session

from geoanalytics.analytics.correlations import (
    _fx_levels,
    _price_levels,
    _returns_by_date,
)
from geoanalytics.core.logging import get_logger
from geoanalytics.storage.models import Asset

log = get_logger("analytics.regimes")

# RiskMetrics λ — как в G1 (indicators.ewma_volatility), единый язык волатильности.
EWMA_LAMBDA = 0.94
# Сколько первых точек EWMA выкидываем как прогрев (оценка ещё не устоялась).
WARMUP = 30
MIN_OBS = 200

REGIME_LABELS = {2: ["спокойный", "кризис"],
                 3: ["спокойный", "повышенный", "кризис"]}


def ewma_vol_series(rets: list[float], lam: float = EWMA_LAMBDA) -> list[float]:
    """Серия дневных EWMA-волатильностей (в %), по точке на доходность."""
    out: list[float] = []
    var = rets[0] ** 2 if rets else 0.0
    for r in rets:
        var = lam * var + (1 - lam) * r * r
        out.append((var ** 0.5) * 100)
    return out


# --------------------------------------------------------------------------- #
# Гауссовский HMM (диагональные ковариации): EM (Баум-Велч) + Витерби.
# --------------------------------------------------------------------------- #

def _log_gauss(x: np.ndarray, means: np.ndarray, variances: np.ndarray) -> np.ndarray:
    """log N(x_t | mean_k, diag var_k) для всех t, k → (T, K)."""
    diff = x[:, None, :] - means[None, :, :]              # (T, K, D)
    return -0.5 * (np.sum(diff**2 / variances[None], axis=2)
                   + np.sum(np.log(2 * np.pi * variances), axis=1)[None])


def fit_hmm(x: np.ndarray, n_states: int = 3, *, n_iter: int = 100,
            tol: float = 1e-5, sticky: float = 0.98):
    """EM для гауссовского HMM. Возвращает (means, variances, trans, pi, loglik).

    Инициализация детерминированная: состояния — квантильные группы по первой
    фиче (vol), переходы — липкие (sticky), что отражает инертность режимов.
    """
    t_len, _ = x.shape
    order = np.argsort(x[:, 0])
    groups = np.array_split(order, n_states)
    means = np.array([x[g].mean(axis=0) for g in groups])
    variances = np.array([x[g].var(axis=0) + 1e-6 for g in groups])
    trans = np.full((n_states, n_states), (1 - sticky) / (n_states - 1))
    np.fill_diagonal(trans, sticky)
    pi = np.full(n_states, 1.0 / n_states)

    prev_ll = -np.inf
    for _ in range(n_iter):
        log_b = _log_gauss(x, means, variances)
        b = np.exp(log_b - log_b.max(axis=1, keepdims=True))

        # Масштабированный forward-backward.
        alpha = np.empty((t_len, n_states))
        scale = np.empty(t_len)
        alpha[0] = pi * b[0]
        scale[0] = alpha[0].sum()
        alpha[0] /= scale[0]
        for t in range(1, t_len):
            alpha[t] = (alpha[t - 1] @ trans) * b[t]
            scale[t] = alpha[t].sum()
            alpha[t] /= scale[t]
        beta = np.ones((t_len, n_states))
        for t in range(t_len - 2, -1, -1):
            beta[t] = (trans @ (b[t + 1] * beta[t + 1])) / scale[t + 1]

        gamma = alpha * beta
        gamma /= gamma.sum(axis=1, keepdims=True)
        # xi суммарно по времени (нужна только сумма для M-шага).
        xi_sum = np.zeros((n_states, n_states))
        for t in range(t_len - 1):
            xi = (alpha[t][:, None] * trans * (b[t + 1] * beta[t + 1])[None])
            xi_sum += xi / xi.sum()

        pi = gamma[0]
        trans = xi_sum / xi_sum.sum(axis=1, keepdims=True)
        w = gamma.sum(axis=0)
        means = (gamma.T @ x) / w[:, None]
        variances = np.array([
            (gamma[:, k][:, None] * (x - means[k]) ** 2).sum(axis=0) / w[k]
            for k in range(n_states)
        ]) + 1e-8

        # Поправка масштабирования: log P(X) = Σ log scale_t + Σ max_t log b.
        ll = float(np.sum(np.log(scale)) + np.sum(log_b.max(axis=1)))
        if abs(ll - prev_ll) < tol:
            break
        prev_ll = ll
    return means, variances, trans, pi, prev_ll


def viterbi(x: np.ndarray, means: np.ndarray, variances: np.ndarray,
            trans: np.ndarray, pi: np.ndarray) -> np.ndarray:
    """Наиболее вероятный путь состояний (T,)."""
    log_b = _log_gauss(x, means, variances)
    log_a = np.log(trans + 1e-300)
    t_len, n = log_b.shape
    delta = np.empty((t_len, n))
    psi = np.zeros((t_len, n), dtype=int)
    delta[0] = np.log(pi + 1e-300) + log_b[0]
    for t in range(1, t_len):
        cand = delta[t - 1][:, None] + log_a
        psi[t] = cand.argmax(axis=0)
        delta[t] = cand.max(axis=0) + log_b[t]
    path = np.empty(t_len, dtype=int)
    path[-1] = delta[-1].argmax()
    for t in range(t_len - 2, -1, -1):
        path[t] = psi[t + 1][path[t + 1]]
    return path


@dataclass
class RegimeResult:
    """Размеченная история режимов + текущий режим."""

    dates: list[date] = field(default_factory=list)
    states: list[int] = field(default_factory=list)     # 0=спокойный … K-1=кризис
    labels: list[str] = field(default_factory=list)     # имена состояний
    state_share: dict[str, float] = field(default_factory=dict)   # доля дней
    state_vol: dict[str, float] = field(default_factory=dict)     # ср. vol IMOEX, %
    current: str = ""
    current_since: date | None = None
    error: str | None = None


def detect_regimes(features: np.ndarray, dates: list[date],
                   n_states: int = 3) -> RegimeResult:
    """Чистое ядро: фит HMM + Витерби + упорядочивание состояний по vol."""
    result = RegimeResult()
    if len(dates) < MIN_OBS:
        result.error = f"мало точек ({len(dates)} < {MIN_OBS})"
        return result
    means, variances, trans, pi, _ = fit_hmm(features, n_states)
    path = viterbi(features, means, variances, trans, pi)

    # Состояния в порядке роста vol (первая фича — log vol IMOEX).
    order = np.argsort(means[:, 0])
    rank = {int(old): new for new, old in enumerate(order)}
    states = [rank[int(s)] for s in path]
    labels = REGIME_LABELS.get(n_states,
                               [f"режим {i}" for i in range(n_states)])

    result.dates, result.states, result.labels = list(dates), states, labels
    arr = np.array(states)
    for k, name in enumerate(labels):
        mask = arr == k
        result.state_share[name] = round(float(mask.mean()), 3)
        # means в лог-пространстве → обратно в проценты.
        result.state_vol[name] = round(float(np.exp(means[order[k], 0])), 2)
    result.current = labels[states[-1]]
    since = len(states) - 1
    while since > 0 and states[since - 1] == states[-1]:
        since -= 1
    result.current_since = dates[since]
    return result


def market_regimes(session: Session, n_states: int = 3) -> RegimeResult:
    """DB-раннер: фичи из IMOEX и USD/RUB → detect_regimes."""
    imoex = session.query(Asset).filter(Asset.ticker == "IMOEX").first()
    if imoex is None:
        return RegimeResult(error="IMOEX не найден (geo backfill -t IMOEX)")
    mkt_rets = _returns_by_date(_price_levels(session, imoex.id))
    fx_rets = _returns_by_date(_fx_levels(session, "USD"))
    common = sorted(set(mkt_rets) & set(fx_rets))
    if len(common) < MIN_OBS + WARMUP:
        return RegimeResult(error=f"мало общих точек IMOEX∩USD ({len(common)})")
    vol_mkt = ewma_vol_series([mkt_rets[d] for d in common])
    vol_fx = ewma_vol_series([fx_rets[d] for d in common])
    features = np.log(np.column_stack([vol_mkt, vol_fx])[WARMUP:] + 1e-12)
    result = detect_regimes(features, common[WARMUP:], n_states)
    if not result.error:
        log.info("regimes_done", current=result.current,
                 since=str(result.current_since), n=len(result.dates))
    return result


def record_regimes(session: Session, n_states: int = 3) -> int:
    """L5: посчитать режимы и идемпотентно записать ВСЮ размеченную историю в `market_regimes`.

    HMM размечает всю историю сразу — таблица наполняется трендом за один прогон (не ждём
    накопления). Возвращает число записанных дней (0 при ошибке HMM)."""
    from geoanalytics.storage.repositories import MarketRegimeRepository

    result = market_regimes(session, n_states=n_states)
    if result.error:
        log.warning("record_regimes_skipped", error=result.error)
        return 0
    rows = [
        (d, s, result.labels[s], result.state_vol.get(result.labels[s]))
        for d, s in zip(result.dates, result.states, strict=False)
    ]
    n = MarketRegimeRepository(session).replace_history(rows)
    log.info("record_regimes_done", days=n, current=result.current)
    return n
