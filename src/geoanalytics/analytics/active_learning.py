"""Активное обучение (ось наблюдаемости I, I5): отбор НИЗКОУВЕРЕННЫХ предсказаний на разметку.

Узкое место качества — объём золота. Вместо случайной разметки берём примеры, где модель
наименее уверена (у границы решения) — их ручная метка даёт максимум информации на единицу
труда. Уверенность берём из УЖЕ хранимых полей: `sentiment_score` ([-1,1], 0 ≈ неуверен) и
`significance` ([0,1], близость к гейту алертов ≈ неуверен). Новых столбцов не нужно.

Чистое ядро уверенности тестируется без БД; обёртка тянет кандидатов из статей за окно.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from geoanalytics.core.logging import get_logger

log = get_logger("analytics.active_learning")

TASKS = ("sentiment", "significance")


def confidence_proxy(task: str, *, sentiment_score: float | None,
                     significance: float | None, gate: float = 0.6) -> float | None:
    """Чистое ядро: близость предсказания к границе решения → «уверенность» [0..1] (больше =
    увереннее). sentiment: |score| (0 — спорный). significance: расстояние до гейта, нормированное
    (0 — ровно на пороге). None — нет нужного поля (кандидат не оценивается)."""
    if task == "sentiment":
        return None if sentiment_score is None else abs(sentiment_score)
    if task == "significance":
        if significance is None:
            return None
        # Расстояние до гейта, нормированное на больший из отрезков [0,gate] / [gate,1].
        span = max(gate, 1.0 - gate) or 1.0
        return abs(significance - gate) / span
    raise ValueError(f"unknown task: {task}")


def low_confidence_candidates(session, *, task: str = "sentiment", threshold: float = 0.25,
                              limit: int = 50, days: int = 30,
                              gate: float = 0.6) -> list[dict]:
    """Статьи с НИЗКОЙ уверенностью предсказания `task` за окно `days` — кандидаты на разметку.

    Возвращает ``[{article_id, title, url, label, score, confidence}]``, увереннейшие В КОНЦЕ
    (наименее уверенные первыми). `threshold` — порог уверенности (берём ниже него)."""
    if task not in TASKS:
        raise ValueError(f"unknown task: {task!r} (ожидается {TASKS})")
    from sqlalchemy import select

    from geoanalytics.storage.models import Article

    since = datetime.now(UTC) - timedelta(days=days)
    field = Article.sentiment_score if task == "sentiment" else Article.significance
    rows = session.execute(
        select(Article.id, Article.title, Article.url, Article.sentiment,
               Article.sentiment_score, Article.significance)
        .where(Article.published_at >= since, field.is_not(None))
    ).all()
    out: list[dict] = []
    for aid, title, url, sentiment, sscore, sig in rows:
        conf = confidence_proxy(task, sentiment_score=sscore, significance=sig, gate=gate)
        if conf is None or conf >= threshold:
            continue
        out.append({
            "article_id": aid, "title": title, "url": url,
            "label": sentiment if task == "sentiment" else _sig_label(sig, gate),
            "score": round(sscore if task == "sentiment" else sig, 4),
            "confidence": round(conf, 4),
        })
    out.sort(key=lambda c: c["confidence"])
    return out[:limit]


def _sig_label(significance: float | None, gate: float) -> str:
    """Бинарная метка значимости по гейту (для показа кандидата)."""
    if significance is None:
        return "—"
    return "значима" if significance >= gate else "фон"
