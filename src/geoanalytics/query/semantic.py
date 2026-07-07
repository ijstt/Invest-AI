"""Семантический поиск по новостям (RAG): pgvector-косинус + лёгкий реранкинг.

Первый потребитель векторов из таблицы `embeddings` (заполняются в M1 при инжесте).
Запрос эмбеддится тем же путём, что и статьи (`get_embedder().embed_one`, без
e5-префиксов — значит пространства запроса и корпуса консистентны), и ранжируется
косинусной близостью (HNSW-индекс `ix_embeddings_vector` на halfvec).

Чистый top-k по косинусу хоронил свежий релевантный материал под чуть более близким, но
старым/незначимым. Поэтому берём шире (k×`_OVERSAMPLE`) и пере-ранжируем комбинацией
близость + свежесть + значимость (`rerank_score`, чистая и тестируемая). Реранкинг не зовёт
модель — только дешёвая арифметика поверх уже полученных кандидатов.

Graceful degradation: если эмбеддер недоступен (нет fastembed/модели), возвращаем
пустой список — вызывающий код (`query/ask.py`) переключается на эвристику.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from geoanalytics.core.logging import get_logger
from geoanalytics.nlp.embeddings import get_embedder
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import Article, Embedding

log = get_logger("query.semantic")

# Во сколько раз шире берём кандидатов из векторного поиска перед реранкингом.
_OVERSAMPLE = 4
# Веса реранкинга: близость доминирует (это релевантность), свежесть и значимость —
# мягкие бусты/тай-брейки, чтобы свежий важный материал не тонул под старым.
_W_SIM, _W_RECENCY, _W_SIG = 0.7, 0.15, 0.15
# Период полураспада свежести (часов): вес свежести падает вдвое каждые столько часов.
_RECENCY_HALF_LIFE_H = 72.0


def rerank_score(similarity: float, significance: float | None, age_hours: float | None) -> float:
    """Комбинированный скор кандидата в [0,1]. Чистая функция (тестируется).

    similarity = 1 - cosine_distance (релевантность), significance ∈ [0,1] (важность),
    свежесть = 0.5**(age/half_life) ∈ (0,1]. Отсутствующие значимость/возраст → нейтральны
    (значимость 0, свежесть как «старое»), близость остаётся главным фактором.
    """
    sig = significance or 0.0
    recency = 0.5 ** (age_hours / _RECENCY_HALF_LIFE_H) if age_hours is not None else 0.0
    return round(_W_SIM * similarity + _W_RECENCY * recency + _W_SIG * sig, 4)


def search_news(question: str, k: int = 8, hours: int | None = None) -> list[dict]:
    """Топ-`k` новостей, релевантных `question`, с лёгким реранкингом.

    Возвращает `[{title, url, sentiment, significance, published_at (ISO), score, rank}]`,
    где `score = similarity` (1 - cosine_distance), `rank` — комбинированный скор реранкинга
    (близость+свежесть+значимость), по которому отсортирован результат. Пустой список, если
    эмбеддер недоступен или подходящих статей нет. `hours` ограничивает окно по `published_at`.
    """
    question = (question or "").strip()
    if not question:
        return []
    embedder = get_embedder()
    if embedder is None:
        log.warning("semantic_no_embedder")
        return []

    qv = embedder.embed_one(question)
    dist = Embedding.vector.cosine_distance(qv).label("d")
    now = datetime.now(UTC)
    with session_scope() as session:
        stmt = select(Article, dist).join(Embedding, Embedding.article_id == Article.id)
        if hours:
            stmt = stmt.where(Article.published_at >= now - timedelta(hours=hours))
        # Берём шире (k×oversample) по косинусу, реранкинг сузит до k.
        rows = session.execute(stmt.order_by(dist).limit(k * _OVERSAMPLE)).all()
        items = []
        for a, d in rows:
            sim = round(1.0 - float(d), 3)
            age_h = ((now - a.published_at).total_seconds() / 3600.0
                     if a.published_at else None)
            items.append({
                "title": a.title,
                "url": a.url,
                "sentiment": a.sentiment,
                "significance": a.significance,
                "published_at": a.published_at.isoformat() if a.published_at else None,
                "score": sim,
                "rank": rerank_score(sim, a.significance, age_h),
            })
    items.sort(key=lambda it: it["rank"], reverse=True)
    return items[:k]
