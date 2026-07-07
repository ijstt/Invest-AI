#!/usr/bin/env python3
"""Бенчмарк инвест-каскада (Фаза 2, шаг 1): пик RSS + скорость на реальных статьях.

Грузит ВСЕ модели, что гоняет processing на статью (significance/events/NER/aspect+
saliency/temporal/sentiment), и прогоняет их по выборке свежих статей из БД. Печатает
пик RSS (VmHWM) и латентность на статью. Ничего не пишет в БД — только чтение текстов.

Запуск НА Pi:  ~/News/.venv/bin/python ~/News/deploy/pi/bench-nlp.py [N]
"""
from __future__ import annotations

import sys
import time
from datetime import date


def _rss_mb() -> float:
    """Пик RSS процесса (VmHWM) в МБ — честный максимум, а не текущий."""
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / 1024
    return 0.0


def _sample_texts(n: int) -> list[str]:
    """Свежие тексты статей из БД; фолбэк — канонический набор, если БД недоступна."""
    try:
        from sqlalchemy import text as sql

        from geoanalytics.storage.db import session_scope

        with session_scope() as s:
            rows = s.execute(
                sql("SELECT title || '. ' || COALESCE(text,'') FROM articles "
                    "WHERE text IS NOT NULL AND length(text) > 200 "
                    "ORDER BY published_at DESC LIMIT :n"),
                {"n": n},
            ).all()
        texts = [r[0] for r in rows if r[0]]
        if texts:
            return texts
    except Exception as exc:  # noqa: BLE001
        print(f"  (БД недоступна, канонический набор: {exc})")
    base = (
        "Сбербанк отчитался о рекордной прибыли за квартал, акции выросли на 3%. "
        "Совет директоров рекомендовал дивиденды выше консенсуса аналитиков. "
        "ЦБ РФ может повысить ключевую ставку на фоне ускорения инфляции."
    )
    return [base] * n


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    print(f"RSS до импортов: {_rss_mb():.0f} MB")

    from geoanalytics.nlp import aspect, classify, ner, temporal
    from geoanalytics.nlp import sentiment
    from geoanalytics.nlp.significance import predict_significance

    print(f"RSS после импортов: {_rss_mb():.0f} MB")

    texts = _sample_texts(n)
    print(f"Статей в выборке: {len(texts)}")
    today = date.today()

    # Прогрев: первая статья грузит все веса в память (ленивые getter'ы).
    print("Прогрев (загрузка весов всех моделей)…")
    t0 = time.monotonic()
    _ = predict_significance(texts[0])
    _ = classify.classify_event(texts[0])
    _ = ner.extract_entities(texts[0])
    _ = temporal.temporal_anchor(texts[0], today)
    _ = aspect.analyze_pair(aspect.aspect_name("SBER", "Сбербанк"), texts[0])
    _ = sentiment.analyze(texts[0])
    warm = time.monotonic() - t0
    print(f"  прогрев: {warm:.1f}s; RSS после загрузки моделей: {_rss_mb():.0f} MB")

    # Замер: полный каскад по всем статьям.
    print("Замер полного каскада…")
    t0 = time.monotonic()
    for txt in texts:
        predict_significance(txt)
        classify.classify_event(txt)
        ner.extract_entities(txt)
        temporal.temporal_anchor(txt, today)
        aspect.analyze_pair(aspect.aspect_name("SBER", "Сбербанк"), txt)
        sentiment.analyze(txt)
    elapsed = time.monotonic() - t0

    print("\n===== ИТОГ =====")
    print(f"Пик RSS (VmHWM):        {_rss_mb():.0f} MB")
    print(f"Статей:                 {len(texts)}")
    print(f"Время каскада:          {elapsed:.1f}s")
    print(f"На статью:              {elapsed / len(texts) * 1000:.0f} ms")
    print(f"Пропускная способность: {len(texts) / elapsed:.1f} статей/с")


if __name__ == "__main__":
    main()
