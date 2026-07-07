"""F6 (Волна 2): кластеризация статей в сюжеты.

Одно событие порождает N статей (разные источники, рерайты, обновления) — без
кластеризации каждый сюжет считается N раз: neg_spike раздувается (Б10), рерайты
проходят хеш-дедуп (Б11). Здесь онлайн-алгоритм:

    для каждой некластеризованной статьи (хронологически):
      ближайшая по эмбеддингу УЖЕ кластеризованная статья в окне ±window —
        cosine-дистанция ≤ порога → присоединяем к её сюжету,
        иначе → статья открывает новый сюжет.

Порог откалиброван на живых данных (2026-06-11, e5-large halfvec): пары одного
сюжета (рерайты «сбили N дронов», прогнозы Грефа) лежат на дистанции ≤0.12,
случайные пары — на 0.22+ (p10 0.219). Берём 0.12: НЕДОслияние безопаснее
ПЕРЕслияния (склейка разных сюжетов травит подсчёты сильнее, чем дубль-сюжет).

Потребители: neg_spike считает УНИКАЛЬНЫЕ сюжеты вместо статей (alerts.engine),
`n_articles`/velocity — будущий сигнал значимости. CLI: `geo stories`.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from config.settings import get_settings
from geoanalytics.core.logging import get_logger
from geoanalytics.storage.models import Article, Embedding, Story

log = get_logger("context.stories")


@dataclass
class StoryAssignResult:
    """Итог прогона кластеризации."""

    assigned: int = 0      # прикреплено к существующим сюжетам
    created: int = 0       # открыто новых сюжетов
    errors: int = 0


def nearest_in_context(vec, context: deque) -> tuple[int, float] | None:
    """(story_id, cosine-дистанция) ближайшего вектора контекста; None — контекст пуст.

    `vec` — нормированный numpy-вектор; `context` — deque (story_id, published_at,
    нормированный вектор). Чистая числовая часть алгоритма (тестируется).
    """
    import numpy as np

    if not context:
        return None
    mat = np.stack([c[2] for c in context])
    dists = 1.0 - mat @ vec
    i = int(np.argmin(dists))
    return context[i][0], float(dists[i])


def _normalized(vector):
    import numpy as np

    # pgvector возвращает HalfVector — numpy его не принимает напрямую.
    if hasattr(vector, "to_numpy"):
        vector = vector.to_numpy()
    v = np.asarray(vector, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n else v


def _load_context(session, since: datetime, window: timedelta) -> deque:
    """Уже кластеризованные статьи окна перед первой некластеризованной —
    стартовый контекст для инкрементального прогона."""
    rows = session.execute(
        select(Article.story_id, Article.published_at, Embedding.vector)
        .join(Embedding, Embedding.article_id == Article.id)
        .where(Article.story_id.is_not(None),
               Article.published_at >= since - window)
        .order_by(Article.published_at.asc())
    ).all()
    return deque((sid, pub, _normalized(vec)) for sid, pub, vec in rows)


def assign_stories(limit: int | None = None, batch_size: int = 1000) -> StoryAssignResult:
    """Кластеризует статьи без сюжета, хронологически. Идемпотентно.

    Алгоритм — онлайн, в памяти (numpy): для статьи ищется ближайшая по cosine
    УЖЕ кластеризованная статья в скользящем окне ±window; ≤ порога — присоединяем
    к её сюжету, иначе статья открывает новый. SQL-вариант (HNSW-запрос на статью)
    на backfill деградировал: фильтр story_id IS NOT NULL по пустой таблице
    заставляет обходить весь граф (~2 ч на 4.5к статей против секунд здесь).

    Статьи без эмбеддинга/даты не кластеризуются (story_id=NULL — потребители
    считают их сюжетами-одиночками через COALESCE; после `geo relink` подтянутся).
    Зовётся каждый цикл scheduler и руками: `geo stories --assign`.
    """
    settings = get_settings()
    threshold = settings.story_distance
    window = timedelta(hours=settings.story_window_hours)
    result = StoryAssignResult()
    from geoanalytics.storage.db import session_scope

    done = 0
    context: deque | None = None  # (story_id, published_at, норм. вектор), по времени
    while limit is None or done < limit:
        take = batch_size if limit is None else min(batch_size, limit - done)
        batch_progress = 0  # защита от вечного цикла: батч без прогресса → стоп
        with session_scope() as session:
            rows = session.execute(
                select(Article, Embedding.vector)
                .join(Embedding, Embedding.article_id == Article.id)
                .where(Article.story_id.is_(None), Article.published_at.is_not(None))
                .order_by(Article.published_at.asc(), Article.id)
                .limit(take)
            ).all()
            if not rows:
                break
            if context is None:
                context = _load_context(session, rows[0][0].published_at, window)
            for article, vector in rows:
                done += 1
                try:
                    vec = _normalized(vector)
                    pub = article.published_at
                    # Скользящее окно: выбрасываем устаревший контекст слева.
                    while context and context[0][1] < pub - window:
                        context.popleft()
                    near = nearest_in_context(vec, context)
                    if near is not None and near[1] <= threshold:
                        story = session.get(Story, near[0])
                        article.story_id = story.id
                        story.n_articles += 1
                        if story.last_seen_at is None or pub > story.last_seen_at:
                            story.last_seen_at = pub
                        result.assigned += 1
                    else:
                        story = Story(title=article.title[:1024],
                                      started_at=pub, last_seen_at=pub)
                        session.add(story)
                        session.flush()
                        article.story_id = story.id
                        result.created += 1
                    context.append((story.id, pub, vec))
                    batch_progress += 1
                except Exception as exc:  # noqa: BLE001 — одна статья не валит батч
                    result.errors += 1
                    log.error("story_assign_failed", article_id=article.id, error=str(exc))
        if not batch_progress:
            # Ни одной успешной кластеризации за батч: те же статьи вернутся из
            # запроса снова — без этого break цикл крутился бы вечно на ошибках.
            log.error("stories_no_progress_abort", errors=result.errors)
            break
        if len(rows) < take:
            break
    log.info("stories_assigned", assigned=result.assigned, created=result.created,
             errors=result.errors)
    return result


def top_stories(hours: int = 48, limit: int = 15) -> list[dict]:
    """Крупнейшие сюжеты за окно: [{id, title, n_articles, started_at, last_seen_at}]."""
    from datetime import UTC, datetime

    from geoanalytics.storage.db import session_scope

    since = datetime.now(UTC) - timedelta(hours=hours)
    with session_scope() as session:
        rows = session.execute(
            select(Story)
            .where(Story.last_seen_at >= since)
            .order_by(Story.n_articles.desc(), Story.last_seen_at.desc())
            .limit(limit)
        ).scalars().all()
        return [{"id": s.id, "title": s.title, "n_articles": s.n_articles,
                 "started_at": s.started_at, "last_seen_at": s.last_seen_at}
                for s in rows]
