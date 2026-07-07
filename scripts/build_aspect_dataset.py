#!/usr/bin/env python
"""Сборка датасетов F1 (aspect-sentiment) и F2 (saliency) из золота Qwen (Волна 2).

Вход: data/aspect_gold.jsonl (scripts/llm_label_aspect.py), хронологический.
Выход (вход = `encode_pair(aspect, text)` — тот же формат, что на инференсе):
    data/aspect_sentiment_{train,eval}.jsonl   label ∈ positive/neutral/negative
    data/saliency_{train,eval}.jsonl           label ∈ salient/background

Сплит временной (последние --eval-frac → eval), дедуп near-дублей до сплита.
В eval-файлы добавляется `article_sentiment` (тональность статьи из БД) — бейзлайн
«копия тональности статьи» (поведение до F1), который модель обязана побить.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from sqlalchemy import select  # noqa: E402

from geoanalytics.nlp.aspect import BACKGROUND, SALIENT, encode_pair  # noqa: E402
from geoanalytics.nlp.dataset import (  # noqa: E402
    dedup_normalized,
    label_distribution,
    time_split,
    write_jsonl,
)
from geoanalytics.storage.db import session_scope  # noqa: E402
from geoanalytics.storage.models import Article  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Датасеты aspect/saliency из золота.")
    parser.add_argument("--gold", default="data/aspect_gold.jsonl")
    parser.add_argument("--eval-frac", type=float, default=0.2)
    args = parser.parse_args()

    gold = [json.loads(x) for x in Path(args.gold).open(encoding="utf-8") if x.strip()]
    if not gold:
        sys.exit(f"Пустое золото: {args.gold}. Сначала llm_label_aspect.py")

    # Тональность статей для бейзлайна (поведение до F1: копия в связи).
    with session_scope() as session:
        art_sent = dict(session.execute(
            select(Article.id, Article.sentiment)
            .where(Article.id.in_({g["article_id"] for g in gold}))
        ).all())

    sent_records, sal_records = [], []
    for g in gold:
        text = encode_pair(g["aspect"], g["text"])
        base = {"text": text, "article_sentiment": art_sent.get(g["article_id"])}
        sent_records.append({**base, "label": g["sentiment"]})
        sal_records.append({**base, "label": SALIENT if g["salient"] else BACKGROUND})

    for task, records in (("aspect_sentiment", sent_records), ("saliency", sal_records)):
        n_before = len(records)
        records = dedup_normalized(records)
        train, eval_ = time_split(records, eval_frac=args.eval_frac)
        # train — строго {text,label} (формат train_lora); extra-поля только в eval.
        train_clean = [{"text": r["text"], "label": r["label"]} for r in train]
        write_jsonl(train_clean, f"data/{task}_train.jsonl")
        write_jsonl(eval_, f"data/{task}_eval.jsonl")
        print(f"{task}: всего {len(records)} (-{n_before - len(records)} дублей); "
              f"train {len(train)} {label_distribution(train)}; "
              f"eval {len(eval_)} {label_distribution(eval_)}")


if __name__ == "__main__":
    main()
