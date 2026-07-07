#!/usr/bin/env python
"""Сборка датасета F3 temporal anchoring из золота Qwen (Волна 3).

Вход: data/temporal_gold.jsonl (scripts/llm_label_temporal.py), хронологический.
Выход: data/temporal_{train,eval}.jsonl, label ∈ past/future/forecast/none.

Сплит временной (последние --eval-frac → eval), дедуп near-дублей до сплита —
тот же рецепт, что в build_aspect_dataset (утечка Б5 закрыта на уровне процесса).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from geoanalytics.nlp.dataset import (  # noqa: E402
    dedup_normalized,
    label_distribution,
    time_split,
    write_jsonl,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Датасет temporal из золота.")
    parser.add_argument("--gold", default="data/temporal_gold.jsonl")
    parser.add_argument("--eval-frac", type=float, default=0.2)
    args = parser.parse_args()

    gold = [json.loads(x) for x in Path(args.gold).open(encoding="utf-8") if x.strip()]
    if not gold:
        sys.exit(f"Пустое золото: {args.gold}. Сначала llm_label_temporal.py")

    records = [{"text": g["text"], "label": g["label"]} for g in gold]
    n_before = len(records)
    records = dedup_normalized(records)
    train, eval_ = time_split(records, eval_frac=args.eval_frac)
    write_jsonl(train, "data/temporal_train.jsonl")
    write_jsonl(eval_, "data/temporal_eval.jsonl")
    print(f"temporal: всего {len(records)} (-{n_before - len(records)} дублей); "
          f"train {len(train)} {label_distribution(train)}; "
          f"eval {len(eval_)} {label_distribution(eval_)}")


if __name__ == "__main__":
    main()
