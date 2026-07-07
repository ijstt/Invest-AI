"""TTL-ретеншн новостей (M6): срок хранения растёт со значимостью.

Идея: важное храним долго, шум — недолго. Срок хранения статьи (дней) линейно
зависит от её значимости:
    ttl_days = round(min_days + (max_days - min_days) · significance)
Статьи старше своего TTL удаляются (каскадно уносят связи и эмбеддинги через
ondelete=CASCADE; события остаются — у них article_id переходит в NULL).

Чистая функция `retention_ttl_days` тестируется без БД; `prune_*` — DB-раннеры.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, delete, func, select

from config.settings import get_settings
from geoanalytics.core.logging import get_logger
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import Article, RawDocument

log = get_logger("retention")


def retention_ttl_days(significance: float, min_days: int, max_days: int) -> int:
    """Срок хранения новости (дней) по её значимости. Чистая функция."""
    sig = max(0.0, min(1.0, significance))
    return round(min_days + (max_days - min_days) * sig)


@dataclass
class PruneResult:
    """Итог чистки: сколько статей и сырых документов удалено (или к удалению при dry-run)."""

    articles: int = 0
    raw_documents: int = 0
    dry_run: bool = False


def _age_days(column):
    """Возраст строки в днях (SQL-выражение) по её timestamp-колонке."""
    return func.extract("epoch", func.now() - column) / 86400.0


def prune(*, dry_run: bool = False) -> PruneResult:
    """Удаляет новости старше их TTL и осиротевшие старые сырые документы.

    `dry_run=True` — только подсчёт кандидатов, без удаления.
    """
    s = get_settings()
    min_d, max_d = s.retention_min_days, s.retention_max_days
    result = PruneResult(dry_run=dry_run)

    with session_scope() as session:
        # TTL по значимости. NULL-значимость (легаси до M6) не трогаем — пусть сперва
        # получит оценку через `geo relink`.
        ttl_days = min_d + (max_d - min_d) * Article.significance
        art_cond = and_(
            Article.significance.isnot(None),
            Article.published_at.isnot(None),
            _age_days(Article.published_at) > ttl_days,
        )
        result.articles = session.scalar(
            select(func.count()).select_from(Article).where(art_cond)
        ) or 0

        # Старые сырые документы, на которые уже не ссылается ни одна статья
        # (например, отфильтрованный на инжесте шум). Рабочему слою не нужны → чистим
        # быстро (raw_retention_days, дефолт 14), не дожидаясь TTL значимых новостей.
        referenced = select(Article.raw_id).where(Article.raw_id.isnot(None))
        raw_cond = and_(
            RawDocument.processed.is_(True),
            _age_days(RawDocument.fetched_at) > s.raw_retention_days,
            RawDocument.id.not_in(referenced),
        )
        result.raw_documents = session.scalar(
            select(func.count()).select_from(RawDocument).where(raw_cond)
        ) or 0

        if not dry_run:
            session.execute(delete(Article).where(art_cond))
            session.execute(delete(RawDocument).where(raw_cond))

    log.info("prune_done", articles=result.articles, raw=result.raw_documents, dry_run=dry_run)
    return result
