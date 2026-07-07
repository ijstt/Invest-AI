"""Сборка обучающих датасетов для дообучения encoder-моделей (M4).

Идея — weak supervision: текущие (правиловые/базовые) предсказания конвейера
используются как «серебряные» метки для дообучения маленьких моделей под домен.
Дальше датасет можно частично разметить вручную и дообучить LoRA-адаптер
(`scripts/train_lora.py`).

Здесь — только чистые функции над строками-словарями (без БД), чтобы логику
формирования датасета было легко тестировать. Выгрузку из БД делает
`scripts/build_dataset.py`.

Каждая запись датасета — `{"text": <str>, "label": <str>}` (формат для
HuggingFace `datasets`/JSONL).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path

from geoanalytics.core.types import EventType
from geoanalytics.nlp.significance import significance_bucket
from geoanalytics.nlp.text import clean_text, normalized_text


def _row_text(row: Mapping) -> str:
    """Текст примера: заголовок + тело, очищенные от HTML/мусора."""
    title = clean_text(row.get("title"))
    body = clean_text(row.get("text"))
    if title and body and body != title:
        return f"{title}. {body}"
    return title or body


def build_sentiment_records(
    rows: Iterable[Mapping], min_confidence: float = 0.3
) -> list[dict]:
    """Записи для дообучения сентимента из строк новостей.

    Берём только уверенные примеры (меньше шума в серебряных метках):
    - позитив/негатив — если |sentiment_score| ≥ `min_confidence`;
    - нейтрал — если |sentiment_score| < `min_confidence`.
    Пустые тексты пропускаются.
    """
    out: list[dict] = []
    for row in rows:
        label = row.get("sentiment")
        text = _row_text(row)
        if not text or not label:
            continue
        score = row.get("sentiment_score")
        confidence = abs(score) if score is not None else 0.0
        if label == "neutral":
            if confidence >= min_confidence:
                continue  # модель «сомневалась» — не лучший нейтральный пример
        elif confidence < min_confidence:
            continue  # слабый позитив/негатив — отбрасываем как шум
        out.append({"text": text, "label": label})
    return out


def build_event_records(rows: Iterable[Mapping], drop_other: bool = True) -> list[dict]:
    """Записи для дообучения классификатора событий.

    По умолчанию исключаем категорию OTHER (доминирующий «мусорный» класс),
    оставляя содержательные типы событий.
    """
    out: list[dict] = []
    for row in rows:
        label = row.get("event_type")
        text = _row_text(row)
        if not text or not label:
            continue
        if drop_other and label == EventType.OTHER.value:
            continue
        out.append({"text": text, "label": label})
    return out


def build_significance_records(rows: Iterable[Mapping]) -> list[dict]:
    """Записи для дообучения классификатора значимости (метки low/medium/high).

    Метка — бакет по слабой метке конвейера `Article.significance` (см.
    `nlp.significance.significance_bucket`). Строки без значимости пропускаются.
    """
    out: list[dict] = []
    for row in rows:
        value = row.get("significance")
        text = _row_text(row)
        if not text or value is None:
            continue
        out.append({"text": text, "label": significance_bucket(float(value))})
    return out


# Метки significance v3 (E3, Волна 1): учитель — РЫНОК, не LLM. Бинарная схема v1:
# за ~6 недель данных |abnormal| ≥ 3% не встречалось ни разу (класс high пуст),
# поэтому low/medium/high пока невозможны — копим исходы и вернёмся к бакетам.
MARKET_MOVED = "moved"
MARKET_FLAT = "flat"
MARKET_MOVE_THRESHOLD_PCT = 1.0


def build_market_significance_records(
    rows: Iterable[Mapping], threshold_pct: float = MARKET_MOVE_THRESHOLD_PCT
) -> list[dict]:
    """Записи для significance v3 (E3): метка от фактической реакции рынка.

    `rows`: {title, text, impact}, где impact — |market-adjusted доходность 1д|
    (по статье с несколькими активами — максимум). label: "moved", если
    |impact| ≥ threshold_pct, иначе "flat". Строки без impact пропускаются.
    """
    out: list[dict] = []
    for row in rows:
        impact = row.get("impact")
        text = _row_text(row)
        if not text or impact is None:
            continue
        label = MARKET_MOVED if abs(float(impact)) >= threshold_pct else MARKET_FLAT
        out.append({"text": text, "label": label})
    return out


def time_split(records: list[dict], eval_frac: float = 0.2) -> tuple[list[dict], list[dict]]:
    """Временной сплит train/eval: последняя доля `eval_frac` → eval.

    Записи должны идти в хронологическом порядке (старое → новое). Временной сплит
    вместо случайного — единственно честный для рыночных меток: случайный позволил бы
    модели «подглядывать» в будущее соседних дней (одни сюжеты в train и eval).
    """
    if not records:
        return [], []
    cut = max(1, int(len(records) * (1 - eval_frac)))
    return records[:cut], records[cut:]


def dedup(records: Iterable[dict]) -> list[dict]:
    """Убирает дубликаты по тексту, сохраняя порядок первого вхождения."""
    seen: set[str] = set()
    out: list[dict] = []
    for rec in records:
        text = rec["text"]
        if text in seen:
            continue
        seen.add(text)
        out.append(rec)
    return out


def dedup_normalized(records: Iterable[dict]) -> list[dict]:
    """Дедуп по НОРМАЛИЗОВАННОМУ тексту (см. `nlp.text.normalized_text`), сохраняя первое
    вхождение. В отличие от `dedup` (точное совпадение) ловит near-дубли — одну новость от
    разных лент/перепостов с косметическими отличиями (`&nbsp;`, регистр, пунктуация).

    Зачем: при обучении near-дубль, разошедшийся по train/eval при `train_test_split`,
    «подсвечивает» eval знакомым примером и завышает eval-метрику (утечка). Дедуп золота
    перед сплитом убирает эту утечку — числа становятся честными."""
    seen: set[str] = set()
    out: list[dict] = []
    for rec in records:
        key = normalized_text(rec["text"])
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


def label_distribution(records: Iterable[dict]) -> dict[str, int]:
    """Сколько примеров на каждую метку (для отчёта о датасете)."""
    dist: dict[str, int] = {}
    for rec in records:
        dist[rec["label"]] = dist.get(rec["label"], 0) + 1
    return dict(sorted(dist.items()))


def write_jsonl(records: Iterable[dict], path: str | Path) -> int:
    """Пишет записи в JSONL (по одной на строку). Возвращает число записей."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            count += 1
    return count


def read_jsonl(path: str | Path) -> list[dict]:
    """Читает датасет из JSONL."""
    with Path(path).open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
