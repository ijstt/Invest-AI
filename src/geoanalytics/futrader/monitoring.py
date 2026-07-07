"""Трек 2 / Пул 9 / D: live-мониторинг торгуемого чемпиона (MLOps жизненного цикла модели).

Train-time строгости (walk-forward, PBO, калибр-гейт) мало для безоператорного созревания: модель
живёт в распределении, которое ДРЕЙФУЕТ. Здесь — наблюдатели ПОСЛЕ деплоя:
  * **PSI** (Population Stability Index) на распределении признаков: baseline (обучающая история) vs
    недавнее live-окно — ловит сдвиг входных данных (concept/feature drift).
  * **Live-калибровка**: Brier/calib-gap по РЕАЛИЗОВАННЫМ бумажным исходам (entry P(win) → исход
    закрытия) — держится ли калибровка вне обучения вживую.
  * **Decay**: реализованный win-rate бумажного счёта vs зарегистрированное OOS-ожидание чемпиона.
При сильном пробое — дрейф-событие + (жёсткие случаи) авто-halt через kill-switch (Пул 9/B) + алерт.
Консервативно (нужен минимум наблюдений), чтобы шум малой выборки не давал ложных стопов.

Чистые ядра (`psi`, `feature_psi`, `paper_calibration`, `decay`) тестируемы; DB-раннер сверху.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Пороги дрейфа (консервативные). PSI>0.25 — заметный сдвиг; >0.5 — сильный.
PSI_WARN = 0.25
PSI_HALT = 0.5
CALIB_HALT = 0.30
DECAY_HALT = 0.20
MIN_LIVE = 20


def psi(baseline: list[float], live: list[float], *, bins: int = 10) -> float | None:
    """Population Stability Index между baseline и live по одному признаку (None если мало точек).

    Бины — по квантилям baseline; PSI = Σ (live% − base%)·ln(live%/base%). Ε-сглаживание пустых
    бинов. PSI<0.1 стабильно; 0.1–0.25 умеренно; >0.25 значимый сдвиг входного распределения."""
    base = [x for x in baseline if x is not None]
    liv = [x for x in live if x is not None]
    if len(base) < MIN_LIVE or len(liv) < MIN_LIVE:
        return None
    ordered = sorted(base)
    edges = [ordered[min(len(ordered) - 1, int(round(q / bins * len(ordered))))]
             for q in range(1, bins)]
    cuts = sorted(set(edges))
    if not cuts:
        return 0.0

    def _hist(xs):
        counts = [0] * (len(cuts) + 1)
        for x in xs:
            idx = 0
            while idx < len(cuts) and x > cuts[idx]:
                idx += 1
            counts[idx] += 1
        n = len(xs)
        return [c / n for c in counts]

    bh, lh = _hist(base), _hist(liv)
    eps = 1e-4
    total = 0.0
    for b, m in zip(bh, lh, strict=False):
        b = max(b, eps)
        m = max(m, eps)
        total += (m - b) * math.log(m / b)
    return total


def feature_psi(baseline_rows: list[dict], live_rows: list[dict],
                feature_names) -> dict[str, float]:
    """PSI по каждому признаку из двух наборов фичей-словарей (пропускает редкие/пустые)."""
    out: dict[str, float] = {}
    for name in feature_names:
        b = [r.get(name) for r in baseline_rows if r.get(name) is not None]
        m = [r.get(name) for r in live_rows if r.get(name) is not None]
        val = psi(b, m)
        if val is not None:
            out[name] = round(val, 3)
    return out


def paper_calibration(pairs: list[tuple]) -> tuple:
    """Brier + calib-gap по парам (p_win, выигрыш∈{0,1}) реализованных бумажных сделок.

    Возвращает (brier, calib_gap, n). None-метрики если пар мало."""
    from geoanalytics.futrader.evaluation import brier_score, calibration_gap

    ps = [p for p, _ in pairs if p is not None]
    ys = [int(y) for p, y in pairs if p is not None]
    if len(ps) < MIN_LIVE:
        return None, None, len(ps)
    bs = brier_score(ys, ps)
    cg = calibration_gap(ys, ps)
    return (round(bs, 4) if bs is not None else None,
            round(cg, 4) if cg is not None else None, len(ps))


def decay(win_rate_live: float | None, win_rate_expected: float | None) -> float | None:
    """Просадка реализованного win-rate относительно OOS-ожидания чемпиона (>0 — деградация)."""
    if win_rate_live is None or win_rate_expected is None:
        return None
    return round(win_rate_expected - win_rate_live, 3)


@dataclass
class DriftReport:
    source: str = ""
    psi_max: float | None = None
    psi_worst_feature: str = ""
    psi_by_feature: dict = field(default_factory=dict)
    live_brier: float | None = None
    live_calib_gap: float | None = None
    n_live_trades: int = 0
    win_rate_live: float | None = None
    win_rate_expected: float | None = None
    win_rate_decay: float | None = None
    should_halt: bool = False
    reasons: tuple = ()
    note: str = ""


def _pair_paper_trades(trades) -> list[tuple]:
    """Спарить entry(P(win))→exit(исход) бумажных сделок по ключу позиции в хронологии."""
    pending: dict[tuple, float] = {}
    pairs: list[tuple] = []
    for t in sorted(trades, key=lambda x: x.ts):
        key = (t.asset_code, t.interval, t.source)
        if t.reason == "entry" and t.p_win is not None:
            pending[key] = t.p_win
        elif t.reason == "exit" and key in pending and t.realized_pnl is not None:
            pairs.append((pending.pop(key), 1 if t.realized_pnl > 0 else 0))
    return pairs


def drift_report(session, *, source: str, account: str = "demo", interval: str = "1h",
                 live_frac: float = 0.3) -> DriftReport:
    """Собрать дрейф-отчёт чемпиона: PSI признаков + live-калибровка + decay (+флаг halt)."""
    from geoanalytics.futrader.policy import FEATURE_ORDER
    from geoanalytics.storage.repositories import (
        FuturesDecisionRepository,
        FuturesModelRunRepository,
        FuturesPaperRepository,
    )

    rep = DriftReport(source=source)
    rows = [r for r in FuturesDecisionRepository(session).labeled(source=source)
            if r.interval == interval]
    rows.sort(key=lambda r: r.ts)
    # PSI: baseline = ранняя история, live = недавний хвост распределения признаков.
    if len(rows) >= 2 * MIN_LIVE:
        cut = int(len(rows) * (1.0 - live_frac))
        base_feats = [r.features or {} for r in rows[:cut]]
        live_feats = [r.features or {} for r in rows[cut:]]
        rep.psi_by_feature = feature_psi(base_feats, live_feats, FEATURE_ORDER)
        if rep.psi_by_feature:
            rep.psi_worst_feature, rep.psi_max = max(
                rep.psi_by_feature.items(), key=lambda kv: kv[1])

    # Live-калибровка + win-rate по реализованным бумажным сделкам этого источника.
    trades = [t for t in FuturesPaperRepository(session).recent_trades(account, limit=5000)
              if t.source == source]
    pairs = _pair_paper_trades(trades)
    rep.live_brier, rep.live_calib_gap, rep.n_live_trades = paper_calibration(pairs)
    if pairs:
        rep.win_rate_live = round(sum(y for _, y in pairs) / len(pairs), 3)

    champ = FuturesModelRunRepository(session).champion(
        source=source, asset_code=None, interval=interval)
    rep.win_rate_expected = getattr(champ, "model_win_rate", None) if champ else None
    rep.win_rate_decay = decay(rep.win_rate_live, rep.win_rate_expected)

    # ВАЖНО: PSI — НАБЛЮДАТЕЛЬ (warning), НЕ триггер halt. Высокий PSI на as-of макро-признаках
    # часто артефакт (признак добавлен в середине истории / смена режима), а не реальная деградация.
    # Авто-halt требует РЕАЛИЗОВАННОГО провала на живых сделках (калибровка/decay при ≥MIN_LIVE).
    reasons: list[str] = []
    if rep.live_calib_gap is not None and rep.live_calib_gap >= CALIB_HALT \
            and rep.n_live_trades >= MIN_LIVE:
        reasons.append(f"калибровка {rep.live_calib_gap:.2f}")
    if rep.win_rate_decay is not None and rep.win_rate_decay >= DECAY_HALT \
            and rep.n_live_trades >= MIN_LIVE:
        reasons.append(f"decay {rep.win_rate_decay:.2f}")
    rep.should_halt = bool(reasons)
    rep.reasons = tuple(reasons)
    rep.note = (f"PSI-фич {len(rep.psi_by_feature)}, live-сделок {rep.n_live_trades}")
    return rep


def run_drift_monitor(session, *, sources, account: str = "demo", interval: str = "1h",
                      auto_halt: bool = True) -> list[DriftReport]:
    """Дрейф-мониторинг по стратегиям; при жёстком дрейфе — halt через kill-switch + алерт."""
    from geoanalytics.core.logging import get_logger
    from geoanalytics.storage.repositories import FuturesRiskStateRepository

    log = get_logger("futrader.monitoring")
    reports = [drift_report(session, source=s, account=account, interval=interval)
               for s in sources]
    halts = [r for r in reports if r.should_halt]
    for r in reports:
        if r.psi_max is not None and r.psi_max >= PSI_WARN:
            log.warning("futrader_drift", source=r.source, psi=r.psi_max,
                        feature=r.psi_worst_feature, calib_gap=r.live_calib_gap,
                        decay=r.win_rate_decay, halt=r.should_halt)
    if auto_halt and halts:
        from geoanalytics.futrader.paper import _dispatch_risk_alert

        reason = "дрейф: " + "; ".join(f"{r.source}[{', '.join(r.reasons)}]" for r in halts)
        repo = FuturesRiskStateRepository(session)
        if not repo.is_halted(account):
            repo.set_state(account, halted=True, reason=reason[:128])
            _dispatch_risk_alert(account, reason[:120])
            log.error("futrader_drift_halt", account=account, reason=reason)
    return reports
