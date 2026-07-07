"""E1 (Волна 1, роудмап v2.0): event study — фактическая ценовая реакция по типам событий.

Агрегирует рыночные исходы новостей (news_outcomes, E2) по типам событий:
- AAR_k  — средний abnormal return на горизонте k (знаковый: направление реакции);
- |AR|_k — средний МОДУЛЬ abnormal return (сила реакции — именно она и есть
  «значимость» типа события);
- hit-rate — доля исходов с |abn_1d| ≥ порога (как часто тип вообще двигает цену).

Из |AR|_5 выводятся ЭМПИРИЧЕСКИЕ веса типов событий (нормировка к максимуму) —
замер вместо мнения: ручной EVENT_WEIGHT (sanctions=1.0 и т.д.) сравнивается с
фактом прямо в отчёте. Контроль конфаундеров: исход исключается, если у того же
актива в ±2 торговых дня были новости ДРУГОГО типа (реакцию нельзя атрибуцировать).

Чистые функции — без БД; DB-раннер `event_study_report` + CLI `geo event-study`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select

from geoanalytics.core.logging import get_logger
from geoanalytics.nlp.significance import EVENT_WEIGHT

log = get_logger("analytics.event_study")

# Минимум исходов на тип события — меньше не статистика, а анекдот.
MIN_SAMPLES = 5
# Порог «новость двинула цену» для hit-rate, % abnormal return за 1 день.
HIT_THRESHOLD_PCT = 2.0
# Окно поиска конфаундеров, ± календарных дней вокруг базовой даты исхода.
# 0 = только коллизии в ОДИН день (1д-доходность буквально общая у двух типов событий).
# Строже (±2) статистически чище, но на плотном новостном потоке голубых фишек
# выкашивает >90% выборки (замер 2026-06-11: 188 из 200) — включается опцией CLI,
# когда исходов накопится на порядок больше.
CONFOUND_WINDOW_DAYS = 0


@dataclass(frozen=True)
class TypeStats:
    """Статистика реакции рынка на один тип события."""

    event_type: str
    n: int
    aar: dict[int, float]        # горизонт → средний знаковый abnormal, %
    mean_abs: dict[int, float]   # горизонт → средний |abnormal|, %
    hit_rate: float              # доля |abn_1d| ≥ HIT_THRESHOLD_PCT

    def as_dict(self) -> dict:
        return {
            "event_type": self.event_type, "n": self.n,
            "aar": {str(k): v for k, v in self.aar.items()},
            "mean_abs": {str(k): v for k, v in self.mean_abs.items()},
            "hit_rate": self.hit_rate,
        }


def confounded_ids(
    rows: list[dict], window_days: int = CONFOUND_WINDOW_DAYS
) -> set:
    """id исходов, рядом с которыми у того же актива были новости ДРУГОГО типа.

    `rows`: {id, asset_id, base_date, event_type}. Если в окне ±window_days
    (календарных — торговый календарь тут избыточен) у актива встречаются исходы
    с другим event_type, реакцию нельзя атрибуцировать одному типу — оба исхода
    исключаются из event study (но остаются в news_outcomes для E3: там тип не нужен).
    """
    by_asset: dict[int, list[dict]] = {}
    for r in rows:
        by_asset.setdefault(r["asset_id"], []).append(r)
    bad: set = set()
    delta = timedelta(days=window_days)
    for items in by_asset.values():
        items.sort(key=lambda r: r["base_date"])
        for i, r in enumerate(items):
            for other in items[i + 1:]:
                if other["base_date"] - r["base_date"] > delta:
                    break
                if other["event_type"] != r["event_type"]:
                    bad.add(r["id"])
                    bad.add(other["id"])
    return bad


def aggregate(rows: list[dict], min_n: int = MIN_SAMPLES,
              hit_threshold: float = HIT_THRESHOLD_PCT) -> list[TypeStats]:
    """Статистика по типам событий из строк исходов.

    `rows`: {event_type, abn_1, abn_3, abn_5} (None в abn — берётся сырой ret_*,
    подставляет вызывающий код). Типы с n < min_n опускаются. Сортировка — по
    убыванию силы реакции (|AR|_5).
    """
    by_type: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("event_type"):
            by_type.setdefault(r["event_type"], []).append(r)

    out: list[TypeStats] = []
    for etype, items in by_type.items():
        vals = {h: [r[f"abn_{h}"] for r in items if r.get(f"abn_{h}") is not None]
                for h in (1, 3, 5)}
        n = len(vals[1])
        if n < min_n:
            continue
        aar = {h: round(sum(v) / len(v), 3) for h, v in vals.items() if v}
        mean_abs = {h: round(sum(abs(x) for x in v) / len(v), 3)
                    for h, v in vals.items() if v}
        hits = sum(1 for x in vals[1] if abs(x) >= hit_threshold)
        out.append(TypeStats(
            event_type=etype, n=n, aar=aar, mean_abs=mean_abs,
            hit_rate=round(hits / n, 3),
        ))
    out.sort(key=lambda s: s.mean_abs.get(5, 0.0), reverse=True)
    return out


def empirical_weights(stats: list[TypeStats], horizon: int = 5) -> dict[str, float]:
    """Эмпирические веса типов событий: |AR|_horizon, нормированный к максимуму → [0,1].

    Это measured-замена ручному EVENT_WEIGHT: вес = насколько тип РЕАЛЬНО двигает
    цены относительно самого «сильного» типа.
    """
    impact = {s.event_type: s.mean_abs.get(horizon) for s in stats
              if s.mean_abs.get(horizon) is not None}
    if not impact:
        return {}
    top = max(impact.values())
    if top <= 0:
        return {}
    return {t: round(v / top, 3) for t, v in impact.items()}


# --------------------------------------------------------------------------- #
# DB-раннер и отчёт.
# --------------------------------------------------------------------------- #
def _load_outcome_rows(session) -> list[dict]:
    """Строки исходов с типом события статьи. abn_* подстраховывается ret_* (нет индекса)."""
    from geoanalytics.storage.models import Article, NewsOutcome

    rows = session.execute(
        select(NewsOutcome.id, NewsOutcome.asset_id, NewsOutcome.base_date,
               Article.event_type,
               NewsOutcome.abn_1d, NewsOutcome.abn_3d, NewsOutcome.abn_5d,
               NewsOutcome.ret_1d, NewsOutcome.ret_3d, NewsOutcome.ret_5d)
        .join(Article, Article.id == NewsOutcome.article_id)
    ).all()
    out: list[dict] = []
    for (oid, asset_id, base_date, etype,
         abn1, abn3, abn5, ret1, ret3, ret5) in rows:
        out.append({
            "id": oid, "asset_id": asset_id,
            "base_date": base_date if isinstance(base_date, date) else base_date.date(),
            "event_type": etype,
            "abn_1": abn1 if abn1 is not None else ret1,
            "abn_3": abn3 if abn3 is not None else ret3,
            "abn_5": abn5 if abn5 is not None else ret5,
        })
    return out


def event_study_report(exclude_confounded: bool = True,
                       min_n: int = MIN_SAMPLES,
                       confound_window: int = CONFOUND_WINDOW_DAYS) -> dict:
    """Полный отчёт event study по накопленным news_outcomes.

    Возвращает {generated_at, total, used, confounded, stats: [...],
    empirical_weights, hand_weights} — пригодно и для CLI-таблицы, и для JSON
    (data/eval/event_study.json, история замеров).
    """
    from datetime import UTC, datetime

    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        rows = _load_outcome_rows(session)
    bad = confounded_ids(rows, window_days=confound_window) if exclude_confounded else set()
    used = [r for r in rows if r["id"] not in bad]
    stats = aggregate(used, min_n=min_n)
    weights = empirical_weights(stats)
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "total": len(rows),
        "used": len(used),
        "confounded": len(bad),
        "min_n": min_n,
        "hit_threshold_pct": HIT_THRESHOLD_PCT,
        "stats": [s.as_dict() for s in stats],
        "empirical_weights": weights,
        "hand_weights": {t: w for t, w in EVENT_WEIGHT.items() if t in weights},
    }
    log.info("event_study_done", total=len(rows), used=len(used),
             confounded=len(bad), types=len(stats))
    return report
