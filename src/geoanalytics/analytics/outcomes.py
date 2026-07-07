"""E2 (Волна 1, роудмап v2.0): рыночная авто-разметка новостей → news_outcomes.

Смена парадигмы: вместо LLM-учителя новости размечает РЫНОК. Для каждой связи
(статья, актив) спустя ≥5 торговых дней фиксируем фактические форвардные
доходности от последнего закрытия ПЕРЕД новостью (pre-news close):

    ret_k = close(base + k) / close(base) − 1,   k ∈ {1, 3, 5} торговых дней
    abn_k = ret_k − β · ret_index_k              (market-adjusted, β к IMOEX)

base — последняя торговая дата СТРОГО ДО торговой даты новости
(`trading_effective_date`: публикация после закрытия сессии → следующий день),
поэтому close(base) гарантированно не знает о новости — без lookahead (Б3).

Чистое ядро (`compute_outcome`, `estimate_beta`) — без БД, основной предмет
тестов. DB-раннер `label_news_outcomes` идемпотентен (UNIQUE article+asset,
ON CONFLICT DO NOTHING): зовётся ежедневно из scheduler и руками `geo outcomes`.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from geoanalytics.core.dates import trading_effective_date
from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import EntityType

log = get_logger("analytics.outcomes")

# Горизонты форвардных доходностей, торговых дней.
HORIZONS = (1, 3, 5)
# Окно оценки беты к индексу (торговых дней до base_date) и минимум наблюдений;
# меньше минимума → β=1.0 (чистый index-adjusted, честнее, чем шумная бета).
BETA_WINDOW = 250
BETA_MIN_OBS = 60


@dataclass(frozen=True)
class Outcome:
    """Рассчитанный рыночный исход одной пары (новость, актив)."""

    base_date: date
    rets: dict[int, float]          # горизонт → доходность, %
    abns: dict[int, float] | None   # горизонт → market-adjusted, %; None — нет индекса
    beta: float | None              # None — данных не хватило, abn считан с β=1


def estimate_beta(asset_rets: list[float], index_rets: list[float]) -> float | None:
    """Бета OLS: cov(asset, index) / var(index). None — мало данных или индекс без дисперсии.

    Пары доходностей должны быть согласованы по датам (формирует вызывающий код).
    """
    n = min(len(asset_rets), len(index_rets))
    if n < BETA_MIN_OBS:
        return None
    a, b = asset_rets[-n:], index_rets[-n:]
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    var_b = sum((x - mean_b) ** 2 for x in b)
    if var_b <= 1e-12:
        return None
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=True))
    return cov / var_b


def _close_at_or_before(dates: list[date], closes: list[float], d: date) -> float | None:
    """Закрытие на дату d или ближайшую предыдущую (для выравнивания индекса по активу)."""
    i = bisect_right(dates, d) - 1
    return closes[i] if i >= 0 else None


def _paired_returns(asset_dates: list[date], asset_closes: list[float], end_idx: int,
                    index_map: dict[date, float]) -> tuple[list[float], list[float]]:
    """Согласованные по датам дневные доходности (актив, индекс) для оценки беты.

    Берётся окно до BETA_WINDOW наблюдений, заканчивающееся на `end_idx` (база
    исхода) — бета оценивается только по данным ДО новости.
    """
    start = max(1, end_idx - BETA_WINDOW + 1)
    a_rets: list[float] = []
    i_rets: list[float] = []
    for t in range(start, end_idx + 1):
        d_prev, d_cur = asset_dates[t - 1], asset_dates[t]
        i_prev, i_cur = index_map.get(d_prev), index_map.get(d_cur)
        if i_prev is None or i_cur is None or not asset_closes[t - 1] or not i_prev:
            continue
        a_rets.append(asset_closes[t] / asset_closes[t - 1] - 1)
        i_rets.append(i_cur / i_prev - 1)
    return a_rets, i_rets


def compute_outcome(
    asset_dates: list[date], asset_closes: list[float], event_date: date,
    index_dates: list[date] | None = None, index_closes: list[float] | None = None,
) -> Outcome | None:
    """Рыночный исход новости с торговой датой `event_date` по ряду актива.

    None — исход ещё не созрел (нет полного горизонта 5 торговых дней) или нет
    pre-news базы (новость старше истории цен). Ряды — старое → новое, дневные.
    """
    if len(asset_dates) < 2:
        return None
    # База: последняя торговая дата СТРОГО ДО торговой даты новости (pre-news close).
    base_idx = bisect_left(asset_dates, event_date) - 1
    if base_idx < 0:
        return None
    last_h = max(HORIZONS)
    if base_idx + last_h >= len(asset_dates):
        return None  # горизонт ещё не наступил — разметим в следующие дни
    base_close = asset_closes[base_idx]
    if not base_close:
        return None

    rets = {
        h: round((asset_closes[base_idx + h] / base_close - 1) * 100, 4)
        for h in HORIZONS
    }

    abns: dict[int, float] | None = None
    beta: float | None = None
    if index_dates and index_closes:
        index_map = dict(zip(index_dates, index_closes, strict=True))
        idx_base = _close_at_or_before(index_dates, index_closes, asset_dates[base_idx])
        if idx_base:
            a_rets, i_rets = _paired_returns(asset_dates, asset_closes, base_idx, index_map)
            beta = estimate_beta(a_rets, i_rets)
            b = beta if beta is not None else 1.0
            abns = {}
            for h in HORIZONS:
                idx_h = _close_at_or_before(
                    index_dates, index_closes, asset_dates[base_idx + h]
                )
                if idx_h is None:
                    continue
                idx_ret = (idx_h / idx_base - 1) * 100
                abns[h] = round(rets[h] - b * idx_ret, 4)
            if not abns:
                abns = None
    return Outcome(base_date=asset_dates[base_idx], rets=rets, abns=abns, beta=beta)


# --------------------------------------------------------------------------- #
# DB-раннер.
# --------------------------------------------------------------------------- #
@dataclass
class LabelResult:
    """Итог прогона разметки."""

    labeled: int = 0      # записано исходов
    pending: int = 0      # горизонт ещё не созрел (созреют в следующие дни)
    no_history: int = 0   # нет pre-news базы в истории цен
    errors: int = 0
    by_asset: dict[str, int] = field(default_factory=dict)


def _series(session: Session, asset_id: int) -> tuple[list[date], list[float]]:
    """Дневной ряд (даты, закрытия) актива, старое → новое."""
    from geoanalytics.storage.models import Price

    rows = session.execute(
        select(Price.ts, Price.close)
        .where(Price.asset_id == asset_id, Price.interval == "1d")
        .order_by(Price.ts)
    ).all()
    return [ts.date() for ts, _ in rows], [float(c) for _, c in rows]


def label_news_outcomes(limit: int | None = None) -> LabelResult:
    """Размечает рыночными исходами все созревшие пары (статья, актив) без исхода.

    Идемпотентно: уже размеченные пары отфильтрованы анти-джойном + UNIQUE-констрейнт.
    Активы-индексы (IMOEX) не размечаются — abnormal против самого себя бессмыслен.
    """
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import (
        Article,
        ArticleEntity,
        Asset,
        NewsOutcome,
    )
    from geoanalytics.storage.seed import BENCHMARK_TICKER

    result = LabelResult()
    with session_scope() as session:
        idx_asset = session.scalars(
            select(Asset).where(Asset.ticker == BENCHMARK_TICKER)
        ).first()
        index_dates, index_closes = (
            _series(session, idx_asset.id) if idx_asset else ([], [])
        )

        stmt = (
            select(ArticleEntity.article_id, ArticleEntity.entity_id,
                   ArticleEntity.relevance, Article.published_at, Asset.ticker)
            .join(Article, Article.id == ArticleEntity.article_id)
            .join(Asset, Asset.id == ArticleEntity.entity_id)
            .outerjoin(NewsOutcome, (NewsOutcome.article_id == ArticleEntity.article_id)
                       & (NewsOutcome.asset_id == ArticleEntity.entity_id))
            .where(
                ArticleEntity.entity_type == EntityType.ASSET.value,
                Article.published_at.is_not(None),
                Asset.kind != "index",
                NewsOutcome.id.is_(None),
            )
            .order_by(Article.published_at)
        )
        if limit:
            stmt = stmt.limit(limit)
        rows = session.execute(stmt).all()

        series_cache: dict[int, tuple[list[date], list[float]]] = {}
        for article_id, asset_id, relevance, published_at, ticker in rows:
            try:
                if asset_id not in series_cache:
                    series_cache[asset_id] = _series(session, asset_id)
                a_dates, a_closes = series_cache[asset_id]
                event_date = trading_effective_date(published_at)
                outcome = compute_outcome(
                    a_dates, a_closes, event_date, index_dates, index_closes
                )
                if outcome is None:
                    # Различаем «созреет позже» и «истории нет вовсе» (для отчёта).
                    if a_dates and event_date > a_dates[0]:
                        result.pending += 1
                    else:
                        result.no_history += 1
                    continue
                values = {
                    "article_id": article_id, "asset_id": asset_id,
                    "event_date": event_date, "base_date": outcome.base_date,
                    "beta": outcome.beta, "relevance": relevance,
                }
                for h in HORIZONS:
                    values[f"ret_{h}d"] = outcome.rets.get(h)
                    values[f"abn_{h}d"] = (outcome.abns or {}).get(h)
                ins = (
                    pg_insert(NewsOutcome).values(**values)
                    .on_conflict_do_nothing(constraint="uq_news_outcome")
                )
                if session.execute(ins).rowcount:
                    result.labeled += 1
                    result.by_asset[ticker] = result.by_asset.get(ticker, 0) + 1
            except Exception as exc:  # noqa: BLE001 — одна пара не валит прогон
                result.errors += 1
                log.error("outcome_label_failed", article_id=article_id,
                          asset_id=asset_id, error=str(exc))
    log.info("outcomes_labeled", labeled=result.labeled, pending=result.pending,
             no_history=result.no_history, errors=result.errors)
    return result
