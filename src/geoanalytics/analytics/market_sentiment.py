"""B1: персистентный индекс настроения рынка (рынок / сектор / актив) во времени.

Раньше сентимент агрегировался на лету (EWMA по активу, [[sentiment_trend]]) и не сохранялся.
Здесь дневной агрегат материализуется в таблицу `market_sentiment`: среднее, EWMA (тональный
моментум), ширина (breadth = доля позитив − негатив), разброс мнений (dispersion), объём и
новостное давление. Накопленный ряд даёт тренд и — в паре с ценой — дивергенцию (цена ↔
настроение). Питает консенсус сводки и рекомендации (Волна C).

Запись идемпотентна: пересчёт дня удаляет его строки и вставляет заново. EWMA при бэкфилле
ведётся по дням по возрастанию (перенос предыдущего значения на каждый ключ области).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Date, delete, select
from sqlalchemy.orm import Session

from geoanalytics.storage.models import (
    Article,
    ArticleEntity,
    Asset,
    Company,
    MarketSentiment,
    Sector,
)

SPAN = 14                       # окно EWMA тонального моментума (дни)
_POS = 0.05                     # порог «позитивной» статьи для breadth
_NEG = -0.05


def _alpha(span: int) -> float:
    return 2.0 / (span + 1)


@dataclass
class SentAgg:
    """Дневной агрегат сентимента по одной области (без EWMA — её добавляет запись)."""

    scope: str                  # market | sector | asset
    asset_id: int | None
    sector: str | None
    sent_mean: float
    breadth: float
    dispersion: float
    n_docs: int
    pressure_sum: float


def _stats(rows: list[tuple[float, float | None]]) -> tuple[float, float, float, int, float]:
    """(mean, breadth, dispersion, n, pressure) по списку (sentiment_score, significance)."""
    scores = [s for s, _ in rows]
    n = len(scores)
    mean = sum(scores) / n
    pos = sum(1 for s in scores if s > _POS)
    neg = sum(1 for s in scores if s < _NEG)
    breadth = (pos - neg) / n
    dispersion = (sum((s - mean) ** 2 for s in scores) / n) ** 0.5
    pressure = sum(float(sig or 0.0) for _, sig in rows)
    return mean, breadth, dispersion, n, pressure


def aggregate_day(session: Session, day: date) -> list[SentAgg]:
    """Агрегаты сентимента за день по областям: рынок (все статьи), активы и секторы (по связям).

    Прогнозы брокеров (`is_forecast`) исключены — они не часть новостного настроения (как в сводке).
    """
    day_col = Article.published_at.cast(Date)
    out: list[SentAgg] = []

    # Рынок: все статьи дня с тональностью.
    market_rows = session.execute(
        select(Article.sentiment_score, Article.significance).where(
            day_col == day,
            Article.sentiment_score.is_not(None),
            Article.is_forecast.is_(False),
        )
    ).all()
    if market_rows:
        m, b, d, n, p = _stats([(float(s), sig) for s, sig in market_rows])
        out.append(SentAgg("market", None, None, m, b, d, n, p))

    # Активы и секторы: салиентные связи статья→актив, сектор через company.sector_id.
    asset_rows = session.execute(
        select(ArticleEntity.entity_id, Article.sentiment_score, Article.significance)
        .join(Article, Article.id == ArticleEntity.article_id)
        .where(
            day_col == day,
            Article.sentiment_score.is_not(None),
            Article.is_forecast.is_(False),
            ArticleEntity.entity_type == "asset",
            ArticleEntity.salient.is_not(False),
        )
    ).all()
    if not asset_rows:
        return out

    asset_ids = {aid for aid, _, _ in asset_rows}
    sector_of = dict(session.execute(
        select(Asset.id, Sector.name)
        .join(Company, Company.id == Asset.company_id)
        .join(Sector, Sector.id == Company.sector_id)
        .where(Asset.id.in_(asset_ids))
    ).all())

    by_asset: dict[int, list] = {}
    by_sector: dict[str, list] = {}
    for aid, score, sig in asset_rows:
        by_asset.setdefault(aid, []).append((float(score), sig))
        sec = sector_of.get(aid)
        if sec:
            by_sector.setdefault(sec, []).append((float(score), sig))

    for aid, rows in by_asset.items():
        m, b, d, n, p = _stats(rows)
        out.append(SentAgg("asset", aid, None, m, b, d, n, p))
    for sec, rows in by_sector.items():
        m, b, d, n, p = _stats(rows)
        out.append(SentAgg("sector", None, sec, m, b, d, n, p))
    return out


def _prev_ewma(session: Session, agg: SentAgg, before: date) -> float | None:
    """Последний сохранённый EWMA для области строго раньше `before` (для переноса при записи)."""
    stmt = (
        select(MarketSentiment.sent_ewma)
        .where(
            MarketSentiment.scope == agg.scope,
            MarketSentiment.day < before,
        )
        .order_by(MarketSentiment.day.desc())
        .limit(1)
    )
    if agg.scope == "asset":
        stmt = stmt.where(MarketSentiment.asset_id == agg.asset_id)
    elif agg.scope == "sector":
        stmt = stmt.where(MarketSentiment.sector == agg.sector)
    return session.scalar(stmt)


def record_day(session: Session, day: date | None = None, span: int = SPAN,
               ewma_cache: dict | None = None) -> int:
    """Считает и сохраняет агрегаты сентимента за день (идемпотентно: пере-удаляет день).

    `ewma_cache` — состояние EWMA по ключам области при бэкфилле (быстрее, чем читать БД на
    каждый день); None — читаем предыдущее значение из БД. Возвращает число записанных строк.
    """
    day = day or (datetime.now(UTC).date() - timedelta(days=1))
    aggs = aggregate_day(session, day)
    session.execute(delete(MarketSentiment).where(MarketSentiment.day == day))
    a = _alpha(span)
    for agg in aggs:
        key = (agg.scope, agg.asset_id, agg.sector)
        prev = ewma_cache.get(key) if ewma_cache is not None else _prev_ewma(session, agg, day)
        ewma = agg.sent_mean if prev is None else a * agg.sent_mean + (1 - a) * prev
        if ewma_cache is not None:
            ewma_cache[key] = ewma
        session.add(MarketSentiment(
            day=day, scope=agg.scope, asset_id=agg.asset_id, sector=agg.sector,
            sent_mean=agg.sent_mean, sent_ewma=ewma, breadth=agg.breadth,
            dispersion=agg.dispersion, n_docs=agg.n_docs, pressure_sum=agg.pressure_sum,
        ))
    session.flush()
    return len(aggs)


def backfill(session: Session, days: int = 30, span: int = SPAN) -> int:
    """Перезаполняет индекс за последние `days` дней (по возрастанию, с переносом EWMA)."""
    today = datetime.now(UTC).date()
    ewma_cache: dict = {}
    total = 0
    for i in range(days, 0, -1):
        total += record_day(session, today - timedelta(days=i), span=span, ewma_cache=ewma_cache)
    return total


# --- чтение (для UI / B2-консенсуса / рекомендаций) -------------------------- #
def latest(session: Session, scope: str, asset_id: int | None = None,
           sector: str | None = None) -> MarketSentiment | None:
    """Последняя сохранённая строка настроения по области."""
    stmt = (select(MarketSentiment).where(MarketSentiment.scope == scope)
            .order_by(MarketSentiment.day.desc()).limit(1))
    if scope == "asset":
        stmt = stmt.where(MarketSentiment.asset_id == asset_id)
    elif scope == "sector":
        stmt = stmt.where(MarketSentiment.sector == sector)
    return session.scalar(stmt)


def series(session: Session, scope: str, asset_id: int | None = None,
           sector: str | None = None, days: int = 60) -> list[MarketSentiment]:
    """Ряд строк настроения области за `days` дней (старое → новое)."""
    since = datetime.now(UTC).date() - timedelta(days=days)
    stmt = (select(MarketSentiment)
            .where(MarketSentiment.scope == scope, MarketSentiment.day >= since)
            .order_by(MarketSentiment.day))
    if scope == "asset":
        stmt = stmt.where(MarketSentiment.asset_id == asset_id)
    elif scope == "sector":
        stmt = stmt.where(MarketSentiment.sector == sector)
    return list(session.scalars(stmt))


def is_divergent(price_change_pct: float | None, sent_ewma: float | None,
                 price_eps: float = 1.0, sent_eps: float = 0.1) -> bool:
    """Дивергенция цена↔настроение: заметное движение цены против знака тонального моментума."""
    if price_change_pct is None or sent_ewma is None:
        return False
    if abs(price_change_pct) < price_eps or abs(sent_ewma) < sent_eps:
        return False
    return (price_change_pct > 0) != (sent_ewma > 0)


def divergence(session: Session, asset_id: int, price_change_pct: float | None) -> dict | None:
    """Сводка дивергенции по активу: знак цены vs тональный моментум (для отчёта/рекомендаций)."""
    row = latest(session, "asset", asset_id=asset_id)
    if row is None:
        return None
    diverging = is_divergent(price_change_pct, row.sent_ewma)
    return {"sent_ewma": round(row.sent_ewma, 3), "breadth": round(row.breadth, 3),
            "price_change_pct": price_change_pct, "diverging": diverging}
