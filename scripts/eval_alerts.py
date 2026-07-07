#!/usr/bin/env python
"""Бэктест качества алертов на истории (Фаза A1 roadmap).

Зачем: пороги алертов (`GEO_ALERT_*`) подобраны на глаз, и мы не знаем, насколько
срабатывания осмысленны - сколько из них реально предшествовало движению цены, а
сколько шум. Этот скрипт прогоняет историю новостей/цен/событий через ЧИСТЫЕ правила
`alerts/rules.py` день за днём и сопоставляет каждый алерт с фактическим движением
цены в окне +N дней после него. Даёт precision (доля «оправдавшихся» алертов), объём
(алертов/день) и таблицу «порог → precision/volume» для data-driven тюнинга.

Метрики «попадания» по типам (намеренно простые и интерпретируемые):
- neg_spike  - всплеск негатива «оправдан», если цена объекта УПАЛА на ≥ move_thr%
  в горизонте (для рынка - средняя по активам);
- new_event  - событие «значимо», если |движение| ≥ move_thr% (двигает в любую сторону);
- price_move - движение «устойчиво», если на следующем горизонте оно ПРОДОЛЖИЛОСЬ в ту
  же сторону (персистентность, а не разворот-шум).

Запуск:
    .venv/bin/python scripts/eval_alerts.py            # baseline по .env + sweep
    .venv/bin/python scripts/eval_alerts.py --horizon-days 3 --move-threshold 2.0
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from sqlalchemy import select

from config.settings import get_settings
from geoanalytics.alerts.rules import (
    negative_spike_alerts,
    new_event_alerts,
    price_move_alerts,
)
from geoanalytics.core.types import EntityType, Sentiment
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import (
    Article,
    ArticleEntity,
    Asset,
    Event,
    EventImpact,
    Price,
)

# --------------------------------------------------------------------------- #
# Чистые функции оценки (тестируются в tests/test_eval.py, без БД).
# --------------------------------------------------------------------------- #


def forward_return(closes: list[tuple[date, float]], asof: date, horizon_days: int) -> float | None:
    """Доходность в % от цены на/до `asof` до цены на/до `asof + horizon_days`.

    `closes` - отсортированный по дате список дневных закрытий одного тикера.
    Вход = последнее закрытие в дату ≤ asof; выход = последнее закрытие в дату ≤
    asof+horizon. None, если входной/выходной цены нет (нет данных вперёд).
    """
    entry = _last_close_on_or_before(closes, asof)
    exit_ = _last_close_on_or_before(closes, asof + timedelta(days=horizon_days))
    if entry is None or exit_ is None or entry == 0:
        return None
    # Выход должен быть СТРОГО позже входа, иначе движения «вперёд» нет.
    if _last_date_on_or_before(closes, asof + timedelta(days=horizon_days)) <= asof:
        return None
    return (exit_ - entry) / entry * 100.0


def _last_close_on_or_before(closes: list[tuple[date, float]], day: date) -> float | None:
    best: float | None = None
    for d, c in closes:
        if d <= day:
            best = c
        else:
            break
    return best


def _last_date_on_or_before(closes: list[tuple[date, float]], day: date) -> date:
    best = date.min
    for d, _ in closes:
        if d <= day:
            best = d
        else:
            break
    return best


def evaluate_hit(
    alert_type: str, prior_sign: int, fwd_ret: float | None, move_threshold: float
) -> bool | None:
    """Оправдался ли алерт по форвардной доходности. None - нет данных для оценки.

    `prior_sign` - знак исходного движения (для price_move: +1/-1); для остальных не важен.
    """
    if fwd_ret is None:
        return None
    if alert_type == "neg_spike":
        return fwd_ret <= -move_threshold
    if alert_type == "new_event":
        return abs(fwd_ret) >= move_threshold
    if alert_type == "price_move":
        # Персистентность: продолжилось ли движение в ту же сторону.
        return fwd_ret * prior_sign > 0 and abs(fwd_ret) >= move_threshold
    return None


@dataclass
class Summary:
    total: int = 0
    scored: int = 0
    hits: int = 0

    @property
    def precision(self) -> float | None:
        return round(self.hits / self.scored, 3) if self.scored else None

    def as_dict(self, days: int) -> dict:
        return {
            "total": self.total,
            "scored": self.scored,
            "hits": self.hits,
            "precision": self.precision,
            "per_day": round(self.total / days, 2) if days else None,
        }


def summarize(records: list[tuple[str, str, bool | None]], days: int) -> dict:
    """records = [(alert_type, severity, hit)]. Сводка overall + по типу + по severity."""
    overall = Summary()
    by_type: dict[str, Summary] = defaultdict(Summary)
    by_sev: dict[str, Summary] = defaultdict(Summary)
    for atype, sev, hit in records:
        for bucket in (overall, by_type[atype], by_sev[sev]):
            bucket.total += 1
            if hit is not None:
                bucket.scored += 1
                bucket.hits += int(hit)
    return {
        "overall": overall.as_dict(days),
        "by_type": {k: v.as_dict(days) for k, v in sorted(by_type.items())},
        "by_severity": {k: v.as_dict(days) for k, v in sorted(by_sev.items())},
    }


# --------------------------------------------------------------------------- #
# Загрузка истории из БД (один раз; пороги применяются поверх в Python).
# --------------------------------------------------------------------------- #


@dataclass
class History:
    closes: dict[str, list[tuple[date, float]]]   # ticker -> [(date, close)]
    trading_days: dict[str, set[date]]            # ticker -> даты со свечой
    articles: list[dict]                          # {published, sentiment, sig, tickers}
    events: list[dict]                            # {occurred, etype, sig, impacts}
    day_range: tuple[date, date]


def load_history() -> History:
    with session_scope() as s:
        price_rows = s.execute(
            select(Asset.ticker, Price.ts, Price.close)
            .join(Asset, Asset.id == Price.asset_id)
            .where(Price.interval == "1d")
            .order_by(Asset.ticker, Price.ts)
        ).all()
        closes: dict[str, list[tuple[date, float]]] = defaultdict(list)
        for ticker, ts, close in price_rows:
            closes[ticker].append((ts.date(), float(close)))
        trading_days = {t: {d for d, _ in v} for t, v in closes.items()}

        # Статьи + их активы (derived-связи asset).
        art_assets: dict[int, list[str]] = defaultdict(list)
        link_rows = s.execute(
            select(ArticleEntity.article_id, Asset.ticker)
            .join(Asset, Asset.id == ArticleEntity.entity_id)
            .where(ArticleEntity.entity_type == EntityType.ASSET.value)
        ).all()
        for aid, ticker in link_rows:
            art_assets[aid].append(ticker)

        art_rows = s.execute(
            select(Article.id, Article.published_at, Article.sentiment, Article.significance)
            .where(Article.published_at.isnot(None))
        ).all()
        articles = [
            {"published": pub, "sentiment": sent, "sig": sig or 0.0,
             "tickers": art_assets.get(aid, [])}
            for aid, pub, sent, sig in art_rows
        ]

        ev_rows = s.execute(
            select(Event.id, Event.occurred_at, Event.event_type, Article.significance)
            .join(Article, Article.id == Event.article_id)
            .where(Event.occurred_at.isnot(None))
        ).all()
        ev_impacts: dict[int, list[dict]] = defaultdict(list)
        imp_rows = s.execute(
            select(EventImpact.event_id, Asset.ticker, EventImpact.direction,
                   EventImpact.magnitude)
            .join(Asset, Asset.id == EventImpact.asset_id)
            .order_by(EventImpact.magnitude.desc())
        ).all()
        for eid, ticker, direction, mag in imp_rows:
            ev_impacts[eid].append({"ticker": ticker, "direction": direction,
                                    "magnitude": float(mag) if mag is not None else 0.0})
        events = [
            {"event_id": eid, "occurred": occ, "event_type": etype, "sig": sig or 0.0,
             "impacts": ev_impacts.get(eid, [])}
            for eid, occ, etype, sig in ev_rows
        ]

    pubs = [a["published"].date() for a in articles]
    day_range = (min(pubs), max(pubs))
    return History(closes, trading_days, articles, events, day_range)


# --------------------------------------------------------------------------- #
# Реплей: построить срезы дня → правила → форвардные попадания.
# --------------------------------------------------------------------------- #


@dataclass
class Thresholds:
    price_pct: float
    neg_count: int
    neg_ratio: float
    min_sig: float
    window_hours: int


def _avg_forward(hist: History, tickers: list[str], asof: date, horizon: int) -> float | None:
    vals = [forward_return(hist.closes[t], asof, horizon) for t in tickers if t in hist.closes]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def run_backtest(hist: History, thr: Thresholds, horizon: int, move_thr: float,
                 require_impact_types: frozenset[str] = frozenset()) -> dict:
    """Прогон всей истории по дням; вернуть сводку метрик для данных порогов.

    `require_impact_types` (D1) — типы событий, для которых new_event-алерт идёт
    только при наличии asset-impact; пусто — гейт выключен.
    """
    start, end = hist.day_range
    fwd_cache: dict[tuple[str, date], float | None] = {}

    def fwd(ticker: str, day: date) -> float | None:
        key = (ticker, day)
        if key not in fwd_cache:
            fwd_cache[key] = forward_return(hist.closes.get(ticker, []), day, horizon)
        return fwd_cache[key]

    records: list[tuple[str, str, bool | None]] = []
    seen_events: set[int] = set()
    all_tickers = list(hist.closes)
    day = start
    days = 0
    while day <= end:
        days += 1
        bucket = day.isoformat()
        midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
        since = midnight - timedelta(hours=thr.window_hours)
        day_end = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=UTC)

        # 1) price_move: close[day] vs предыдущая свеча.
        moves = []
        for ticker, closes in hist.closes.items():
            if day not in hist.trading_days[ticker]:
                continue
            cur = _last_close_on_or_before(closes, day)
            prev = _last_close_on_or_before(closes, day - timedelta(days=1))
            if cur is not None and prev:
                moves.append({"ticker": ticker, "change_pct": round((cur - prev) / prev * 100, 2),
                              "last": cur})
        for al in price_move_alerts(moves, thr.price_pct, bucket):
            sign = 1 if al.payload.get("change_pct", 0) > 0 else -1
            records.append((al.alert_type, al.severity,
                            evaluate_hit("price_move", sign, fwd(al.ticker, day), move_thr)))

        # 2) neg_spike: значимые статьи в окне → рынок + по активам.
        win = [a for a in hist.articles
               if since <= a["published"] <= day_end and a["sig"] >= thr.min_sig]
        neg_v = Sentiment.NEGATIVE.value
        scopes: list[dict] = []
        if win:
            scopes.append({"scope": "MARKET", "ticker": None, "total": len(win),
                           "negative": sum(a["sentiment"] == neg_v for a in win)})
        per_asset: dict[str, list[dict]] = defaultdict(list)
        for a in win:
            for t in a["tickers"]:
                per_asset[t].append(a)
        for t, arts in per_asset.items():
            scopes.append({"scope": t, "ticker": t, "total": len(arts),
                           "negative": sum(a["sentiment"] == neg_v for a in arts)})
        for al in negative_spike_alerts(scopes, thr.neg_count, thr.neg_ratio, bucket):
            ret = (fwd(al.ticker, day) if al.ticker
                   else _avg_forward(hist, all_tickers, day, horizon))
            records.append((al.alert_type, al.severity,
                            evaluate_hit("neg_spike", -1, ret, move_thr)))

        # 3) new_event: новые значимые события в окне (дедуп по event_id навсегда).
        evs = []
        for e in hist.events:
            if e["event_id"] in seen_events:
                continue
            if since <= e["occurred"] <= day_end and e["sig"] >= thr.min_sig:
                evs.append(e)
                seen_events.add(e["event_id"])
        for al in new_event_alerts(evs, require_impact_types=require_impact_types):
            ret = fwd(al.ticker, day) if al.ticker else None
            records.append((al.alert_type, al.severity,
                            evaluate_hit("new_event", 0, ret, move_thr)))

        day += timedelta(days=1)

    return summarize(records, days)


def main() -> None:
    ap = argparse.ArgumentParser(description="Бэктест precision алертов на истории.")
    ap.add_argument("--horizon-days", type=int, default=3, help="Окно форвардной доходности.")
    ap.add_argument("--move-threshold", type=float, default=2.0, help="Порог |движения|, %% (hit).")
    ap.add_argument("--out", default="data/eval/alerts_baseline.json")
    args = ap.parse_args()

    s = get_settings()
    base = Thresholds(
        price_pct=s.alert_price_pct, neg_count=s.alert_neg_count,
        neg_ratio=s.alert_neg_ratio, min_sig=s.alert_min_significance,
        window_hours=s.alert_window_hours,
    )
    print("Загрузка истории из БД…")
    hist = load_history()
    d0, d1 = hist.day_range
    print(f"Диапазон новостей: {d0}..{d1} ({(d1 - d0).days + 1} дн), активов: {len(hist.closes)}")
    print(f"Горизонт: {args.horizon_days} дн, порог движения: {args.move_threshold}%\n")

    print("=== BASELINE (.env, без гейта new_event) ===")
    baseline = run_backtest(hist, base, args.horizon_days, args.move_threshold)
    _print_summary(baseline)

    # D1: сравнение new_event без гейта vs с гейтом asset-impact (из настроек).
    gate = get_settings().require_impact_type_set
    d1 = None
    if gate:
        print(f"\n=== D1 GATE require_impact_types={sorted(gate)} ===")
        d1 = run_backtest(hist, base, args.horizon_days, args.move_threshold,
                          require_impact_types=gate)
        ne_before = baseline["by_type"].get("new_event", {})
        ne_after = d1["by_type"].get("new_event", {})
        print(f"  new_event ДО:    total={ne_before.get('total')} "
              f"vol/day={ne_before.get('per_day')} precision={ne_before.get('precision')}")
        print(f"  new_event ПОСЛЕ: total={ne_after.get('total')} "
              f"vol/day={ne_after.get('per_day')} precision={ne_after.get('precision')}")
        print("  overall ПОСЛЕ:")
        _print_summary(d1)

    # Sweep: варьируем по одному порогу, держа остальные на baseline.
    def _bt(thr: Thresholds) -> dict:
        return run_backtest(hist, thr, args.horizon_days, args.move_threshold)

    sweep: dict[str, list[dict]] = {}
    for ratio in (0.4, 0.5, 0.6, 0.7):
        thr = Thresholds(base.price_pct, base.neg_count, ratio, base.min_sig, base.window_hours)
        r = _bt(thr)["by_type"].get("neg_spike")
        sweep.setdefault("neg_ratio", []).append({"value": ratio, **(r or {})})
    for sig in (0.4, 0.5, 0.6, 0.85):
        thr = Thresholds(base.price_pct, base.neg_count, base.neg_ratio, sig, base.window_hours)
        sweep.setdefault("min_sig", []).append({"value": sig, "overall": _bt(thr)["overall"]})
    for pct in (3.0, 5.0, 7.0):
        thr = Thresholds(pct, base.neg_count, base.neg_ratio, base.min_sig, base.window_hours)
        r = _bt(thr)["by_type"].get("price_move")
        sweep.setdefault("price_pct", []).append({"value": pct, **(r or {})})

    def _sweep_row(label: str, row: dict) -> str:
        return (f"  {label}={row['value']}: prec={row.get('precision')} "
                f"vol/day={row.get('per_day')} n={row.get('total')}")

    print("\n=== SWEEP neg_ratio (по neg_spike) ===")
    for row in sweep["neg_ratio"]:
        print(_sweep_row("ratio", row))
    print("=== SWEEP price_pct (по price_move) ===")
    for row in sweep["price_pct"]:
        print(_sweep_row("pct", row))
    print("=== SWEEP min_sig (overall) ===")
    for row in sweep["min_sig"]:
        o = row["overall"]
        print(f"  sig={row['value']}: prec={o['precision']} vol/day={o['per_day']} n={o['total']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generated_at": datetime.now(UTC).isoformat(),
        "horizon_days": args.horizon_days,
        "move_threshold": args.move_threshold,
        "baseline_thresholds": vars(base),
        "baseline": baseline,
        "d1_gate": {"require_impact_types": sorted(gate), "result": d1} if gate else None,
        "sweep": sweep,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nСохранено: {out}")


def _print_summary(r: dict) -> None:
    o = r["overall"]
    print(f"  overall: precision={o['precision']} hits={o['hits']}/{o['scored']} "
          f"total={o['total']} vol/day={o['per_day']}")
    for atype, v in r["by_type"].items():
        print(f"    {atype:11s}: precision={v['precision']} hits={v['hits']}/{v['scored']} "
              f"total={v['total']} vol/day={v['per_day']}")


if __name__ == "__main__":
    main()
