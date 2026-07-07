"""G6: тональный моментум по активу (EWMA суточного сентимента).

Показывает направление тренда настроений по новостям актива.
Положительное значение — устойчивый позитивный фон, отрицательное — негативный.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Date, func, select

from geoanalytics.analytics.indicators import _ema_series
from geoanalytics.storage.models import Article, ArticleEntity


def sentiment_momentum(
    session,
    asset_id: int,
    days: int = 60,
    span: int = 14,
) -> list[tuple[date, float]]:
    """EWMA суточного среднего sentiment_score по активу.

    Возвращает список (дата, ema_значение) — только для дат с достаточной историей
    (span баров назад). Пустой список, если данных меньше чем span дней.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    day_col = Article.published_at.cast(Date)
    rows = session.execute(
        select(
            day_col.label("day"),
            func.avg(Article.sentiment_score).label("avg_sent"),
        )
        .join(ArticleEntity, ArticleEntity.article_id == Article.id)
        .where(
            ArticleEntity.entity_type == "asset",
            ArticleEntity.entity_id == asset_id,
            ArticleEntity.salient.is_not(False),
            Article.published_at >= since,
            Article.sentiment_score.is_not(None),
        )
        .group_by(day_col)
        .order_by(day_col)
    ).all()

    if not rows:
        return []

    dates = [r.day if isinstance(r.day, date) else r.day.date() for r in rows]
    values = [float(r.avg_sent) for r in rows]

    if len(values) < span:
        return list(zip(dates, values))

    ema = _ema_series(values, span)
    offset = len(values) - len(ema)
    return list(zip(dates[offset:], ema))


def latest_momentum(session, asset_id: int, span: int = 14) -> float | None:
    """Последнее значение EWMA тонального моментума (скаляр для отчёта)."""
    series = sentiment_momentum(session, asset_id, days=span * 3, span=span)
    return series[-1][1] if series else None
