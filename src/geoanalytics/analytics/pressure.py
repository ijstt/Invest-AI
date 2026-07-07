"""G5: индекс новостного давления по активу.

Показывает, насколько интенсивен и значим новостной поток за скользящее окно.
Считается on-the-fly из article_entities + articles, таблица не нужна.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from geoanalytics.storage.models import Article, ArticleEntity


def news_pressure(
    session,
    asset_id: int,
    date: datetime | None = None,
    window: int = 7,
) -> float:
    """Индекс новостного давления: Σ significance салиентных статей за window дней.

    Нормируется на ширину окна, чтобы значения были сравнимы при разных window.
    Диапазон: 0 (нет новостей) — ~1+ (очень плотный поток высокозначимых).
    """
    end = date if date is not None else datetime.now(UTC)
    since = end - timedelta(days=window)
    row = session.execute(
        select(func.coalesce(func.sum(Article.significance), 0.0))
        .join(ArticleEntity, ArticleEntity.article_id == Article.id)
        .where(
            ArticleEntity.entity_type == "asset",
            ArticleEntity.entity_id == asset_id,
            ArticleEntity.salient.is_not(False),
            Article.published_at >= since,
            Article.published_at <= end,
            Article.significance.is_not(None),
        )
    ).scalar()
    return float(row) / window
