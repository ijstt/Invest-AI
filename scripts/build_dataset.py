#!/usr/bin/env python
"""Выгрузка обучающих датасетов из БД для дообучения моделей (M4).

Берёт обработанные новости (`articles`) и формирует два JSONL-датасета по
серебряным меткам конвейера:
- `sentiment_dataset.jsonl` — тональность (positive/neutral/negative);
- `events_dataset.jsonl`    — тип события (sanctions/dividends/...).

Это weak supervision: метки получены текущими (правиловыми/базовыми) моделями.
Перед дообучением датасет полезно частично проверить/разметить вручную.

Запуск:
    python scripts/build_dataset.py --out-dir data --min-confidence 0.3
Дальше обучение адаптера: см. scripts/train_lora.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Запуск как `python scripts/build_dataset.py` — добавляем корень репозитория в путь,
# чтобы импортировались пакеты `config` (корень) и `geoanalytics` (src/).
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from sqlalchemy import select

from geoanalytics.nlp.dataset import (
    build_event_records,
    build_sentiment_records,
    build_significance_records,
    dedup,
    label_distribution,
    write_jsonl,
)
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import Article


def _load_rows() -> list[dict]:
    """Читает поля новостей, нужные для разметки, как список словарей."""
    with session_scope() as session:
        rows = session.execute(
            select(
                Article.title,
                Article.text,
                Article.sentiment,
                Article.sentiment_score,
                Article.event_type,
                Article.significance,
            )
        ).all()
    return [
        {
            "title": title,
            "text": text,
            "sentiment": sentiment,
            "sentiment_score": score,
            "event_type": event_type,
            "significance": significance,
        }
        for (title, text, sentiment, score, event_type, significance) in rows
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Сборка обучающих датасетов из БД (M4).")
    parser.add_argument("--out-dir", default="data", help="Каталог для JSONL-файлов.")
    parser.add_argument(
        "--min-confidence", type=float, default=0.3,
        help="Порог уверенности сентимента для отбора примеров.",
    )
    args = parser.parse_args()

    rows = _load_rows()
    print(f"Загружено новостей: {len(rows)}")

    out_dir = Path(args.out_dir)
    sentiment = dedup(build_sentiment_records(rows, min_confidence=args.min_confidence))
    events = dedup(build_event_records(rows))
    significance = dedup(build_significance_records(rows))

    n_sent = write_jsonl(sentiment, out_dir / "sentiment_dataset.jsonl")
    n_event = write_jsonl(events, out_dir / "events_dataset.jsonl")
    n_sig = write_jsonl(significance, out_dir / "significance_dataset.jsonl")

    print(f"Сентимент:  {n_sent} примеров → {label_distribution(sentiment)}")
    print(f"События:    {n_event} примеров → {label_distribution(events)}")
    print(f"Значимость: {n_sig} примеров → {label_distribution(significance)}")


if __name__ == "__main__":
    main()
