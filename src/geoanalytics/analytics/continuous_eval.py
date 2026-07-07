"""Непрерывная оценка моделей + дрейф-алерт (ось наблюдаемости I, I2).

Учитель — РЫНОК (как E2/E3): сравниваем гейт значимости (significance ≥ порог алертов) с
ФАКТИЧЕСКОЙ реакцией цены из `news_outcomes` (|abn_1d| ≥ порог «двинула»). Каждый прогон пишет
метрику в `eval_runs`; накопленный ряд позволяет ловить ДРЕЙФ качества во времени и слать алерт
при деградации относительно трейлинг-базы (среднее предыдущих прогонов).

Чистое ядро (метрики/дрейф) тестируется без БД; DB-обёртка тянет пары и пишет/алертит.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from geoanalytics.core.logging import get_logger

log = get_logger("analytics.continuous_eval")

MARKET_MOVE_PCT = 1.0          # |abn_1d| ≥ 1% — рынок «двинулся» (как build_market_dataset)
DRIFT_REL_TOL = 0.15           # падение метрики > 15% от базы → дрейф-алерт
DRIFT_MIN_HISTORY = 3          # минимум предыдущих прогонов для устойчивой базы
EVAL_MIN_SAMPLES = 30          # меньше — метрика шумна, прогон не пишем


@dataclass(frozen=True)
class MarketAgreement:
    """Согласие гейта значимости с реакцией рынка (на множестве размеченных исходов)."""

    n: int = 0                 # всего пар (исходов) в окне
    n_flagged: int = 0         # модель пометила значимыми (significance ≥ gate)
    n_moved: int = 0           # рынок реально двинулся (|abn_1d| ≥ MARKET_MOVE_PCT)
    precision: float | None = None   # доля «двинувшихся» среди помеченных
    recall: float | None = None      # доля помеченных среди «двинувшихся»


def market_agreement(pairs: list[tuple[float | None, float | None]], *,
                     gate: float, move_pct: float = MARKET_MOVE_PCT) -> MarketAgreement:
    """Чистое ядро: пары ``(significance, abn_1d_pct)`` → precision/recall гейта против рынка.

    Пары с отсутствующей значимостью или доходностью пропускаются. precision/recall = None при
    нулевом знаменателе (нет помеченных / нет двинувшихся)."""
    flagged = moved = tp = n = 0
    for sig, abn in pairs:
        if sig is None or abn is None:
            continue
        n += 1
        is_flag = sig >= gate
        is_move = abs(abn) >= move_pct
        flagged += is_flag
        moved += is_move
        tp += is_flag and is_move
    return MarketAgreement(
        n=n, n_flagged=flagged, n_moved=moved,
        precision=(tp / flagged) if flagged else None,
        recall=(tp / moved) if moved else None,
    )


@dataclass(frozen=True)
class DriftCheck:
    drifted: bool = False
    baseline: float | None = None      # трейлинг-база (среднее предыдущих)
    drop_pct: float | None = None      # относительное падение текущей метрики, %
    reason: str = ""


def check_drift(current: float | None, history: list[float], *,
                rel_tol: float = DRIFT_REL_TOL,
                min_history: int = DRIFT_MIN_HISTORY) -> DriftCheck:
    """Чистое ядро дрейфа: текущее значение vs среднее предыдущих прогонов.

    Дрейф = текущее ниже базы более чем на `rel_tol` (относительно базы), при достаточной
    истории. `history` — значения ПРЕДЫДУЩИХ прогонов (без текущего). Недостаточно истории/
    нет значения → не дрейф (накапливаем базу)."""
    if current is None or len(history) < min_history:
        return DriftCheck(reason="недостаточно истории для базы")
    baseline = sum(history) / len(history)
    if baseline <= 0:
        return DriftCheck(baseline=baseline, reason="вырожденная база")
    drop = (baseline - current) / baseline
    if drop > rel_tol:
        reason = f"метрика {current:.3f} ниже базы {baseline:.3f} на {drop * 100:.0f}%"
        return DriftCheck(drifted=True, baseline=baseline, drop_pct=round(drop * 100, 1),
                          reason=reason)
    return DriftCheck(baseline=baseline, drop_pct=round(drop * 100, 1),
                      reason="в пределах нормы")


@dataclass
class EvalSummary:
    model: str = "significance"
    agreement: MarketAgreement = field(default_factory=MarketAgreement)
    drift: DriftCheck = field(default_factory=DriftCheck)
    recorded: bool = False
    alerted: bool = False
    note: str = ""


def _significance_market_pairs(session, days: int) -> list[tuple[float | None, float | None]]:
    """Пары (significance статьи, abn_1d из news_outcomes) за окно `days` по дате события."""
    from sqlalchemy import select

    from geoanalytics.storage.models import Article, NewsOutcome

    since = (datetime.now(UTC) - timedelta(days=days)).date()
    rows = session.execute(
        select(Article.significance, NewsOutcome.abn_1d)
        .join(Article, Article.id == NewsOutcome.article_id)
        .where(NewsOutcome.event_date >= since)
    ).all()
    return [(s, a) for s, a in rows]


def run_continuous_eval(session, *, days: int = 90, send_alerts: bool = True) -> EvalSummary:
    """Прогон I2: согласие значимости с рынком → запись в `eval_runs` + дрейф-алерт.

    Метрика дрейфа — market_precision (доля реально двинувших среди помеченных значимыми). При
    деградации относительно трейлинг-базы шлёт алерт `model_drift` (дедуп по ISO-неделе)."""
    from geoanalytics.nlp.significance import significance_gates
    from geoanalytics.storage.repositories import EvalRunRepository

    gate = min(significance_gates().values()) if significance_gates() else 0.6
    pairs = _significance_market_pairs(session, days)
    agg = market_agreement(pairs, gate=gate)
    summary = EvalSummary(agreement=agg)
    if agg.n < EVAL_MIN_SAMPLES or agg.precision is None:
        summary.note = f"мало данных (n={agg.n}) — прогон не записан"
        log.info("continuous_eval_skipped", n=agg.n, gate=gate)
        return summary

    repo = EvalRunRepository(session)
    # История ДО записи текущего — база дрейфа из предыдущих прогонов.
    prior = [v for _d, v, _n in repo.recent("significance", "market_precision", limit=12)]
    repo.record("significance", "market_precision", agg.precision, agg.n_flagged, days)
    if agg.recall is not None:
        repo.record("significance", "market_recall", agg.recall, agg.n_moved, days)
    summary.recorded = True
    summary.drift = check_drift(agg.precision, prior)
    log.info("continuous_eval", model="significance", precision=round(agg.precision, 3),
             recall=None if agg.recall is None else round(agg.recall, 3), n=agg.n,
             flagged=agg.n_flagged, moved=agg.n_moved, drifted=summary.drift.drifted)
    if send_alerts and summary.drift.drifted:
        summary.alerted = _emit_drift_alert("significance", "market_precision",
                                            agg.precision, summary.drift)
    return summary


def _stance_market_pairs(session, days: int) -> list[tuple[float, float | None]]:
    """Пары (балл текущей стойки C1, средняя реализованная abn_5d% актива за окно) по акциям.

    Контемпоральный кросс-секционный срез согласия (как significance market_precision): НЕ
    форвард-прогноз, а калибровка «направленная стойка ↔ реализованная аномальная доходность»,
    трекаемая во времени для дрейфа. Стойка считается лайтово (без бэктеста/фундаменталки).
    Стойки исторически не хранятся → используем текущую стойку против недавней реализации."""
    from sqlalchemy import func, select

    from geoanalytics.analytics.prices import asset_indicators
    from geoanalytics.analytics.recommendation import stance_for_asset
    from geoanalytics.storage.models import Asset, NewsOutcome

    since = (datetime.now(UTC) - timedelta(days=days)).date()
    rows = session.execute(
        select(NewsOutcome.asset_id, func.avg(NewsOutcome.abn_5d))
        .where(NewsOutcome.event_date >= since, NewsOutcome.abn_5d.isnot(None))
        .group_by(NewsOutcome.asset_id)
    ).all()
    pairs: list[tuple[float, float | None]] = []
    for asset_id, abn in rows:
        asset = session.get(Asset, asset_id)
        if asset is None or asset.kind in ("fund", "index"):   # MMF/индекс — без TA-стойки
            continue
        try:
            ind = asset_indicators(session, asset.id).as_dict()
            if not ind:
                continue
            st = stance_for_asset(session, asset.id, asset.ticker, indicators=ind,
                                  with_backtest=False, with_fundamentals=False)
        except Exception as exc:  # noqa: BLE001 — один актив не валит срез
            log.warning("stance_eval_skip", asset_id=asset_id, error=str(exc))
            continue
        pairs.append((st.score, None if abn is None else float(abn)))
    return pairs


def run_stance_eval(session, *, days: int = 90, min_samples: int = 10,
                    send_alerts: bool = True) -> EvalSummary:
    """Прогон калибровки направленной стойки (C1): согласие знака стойки с реализованным движением
    рынка → `eval_runs`(model='stance', metric='directional_precision') + дрейф-алерт.

    Кросс-секционная вселенная мала (~акции), поэтому `min_samples` ниже значимостного."""
    from geoanalytics.analytics.recommendation import directional_precision
    from geoanalytics.storage.repositories import EvalRunRepository

    pairs = _stance_market_pairs(session, days)
    res = directional_precision(pairs)
    agg = MarketAgreement(n=len(pairs), n_flagged=res["n"], precision=res["precision"])
    summary = EvalSummary(model="stance", agreement=agg)
    if res["n"] < min_samples or res["precision"] is None:
        summary.note = f"мало направленных пар (n={res['n']}) — прогон не записан"
        log.info("stance_eval_skipped", n_dir=res["n"], pairs=len(pairs))
        return summary

    repo = EvalRunRepository(session)
    prior = [v for _d, v, _n in repo.recent("stance", "directional_precision", limit=12)]
    repo.record("stance", "directional_precision", res["precision"], res["n"], days)
    summary.recorded = True
    summary.drift = check_drift(res["precision"], prior)
    log.info("stance_eval", precision=round(res["precision"], 3), n_dir=res["n"],
             pairs=len(pairs), drifted=summary.drift.drifted)
    if send_alerts and summary.drift.drifted:
        summary.alerted = _emit_drift_alert("stance", "directional_precision",
                                            res["precision"], summary.drift)
    return summary


def _emit_drift_alert(model: str, metric: str, value: float, drift: DriftCheck) -> bool:
    """Алерт деградации модели (дедуп по ISO-неделе — не спамим один и тот же дрейф)."""
    from config.settings import get_settings
    from geoanalytics.alerts import channels
    from geoanalytics.alerts.engine import _insert_new
    from geoanalytics.alerts.rules import Alert
    from geoanalytics.storage.db import session_scope

    iso = datetime.now(UTC).isocalendar()
    alert = Alert(
        alert_type="model_drift",
        severity="warning",
        title=f"Дрейф качества модели: {model}",
        message=(f"Метрика {metric}={value:.3f} ниже трейлинг-базы "
                 f"{drift.baseline:.3f} на {drift.drop_pct:.0f}%. {drift.reason}."),
        dedup_key=f"model_drift:{model}:{metric}:{iso.year}-W{iso.week:02d}",
        payload={"model": model, "metric": metric, "value": value,
                 "baseline": drift.baseline, "drop_pct": drift.drop_pct},
    )
    with session_scope() as session:
        rec_id = _insert_new(session, alert)
    if rec_id is None:
        return False
    channels.dispatch(alert, get_settings())
    log.info("model_drift_alert", model=model, metric=metric, drop_pct=drift.drop_pct)
    return True
