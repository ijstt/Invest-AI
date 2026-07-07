#!/usr/bin/env python
"""Eval F1 (aspect-sentiment) и F2 (saliency) на временно́м hold-out (Волна 2).

F1: модель обязана побить бейзлайн «копия тональности статьи» (поведение до F1,
поле article_sentiment в eval-файле) — иначе деплой бессмыслен.
F2: бейзлайн — мажоритарный класс.

Запуск:
    python scripts/eval_aspect.py --task aspect_sentiment \
        --model data/adapters/aspect-sentiment-v1 [--save data/eval/aspect_sentiment.json]
    python scripts/eval_aspect.py --task saliency --model data/adapters/saliency-v1
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from geoanalytics.nlp.dataset import read_jsonl  # noqa: E402


def macro_metrics(gold: list[str], pred: list[str], labels: list[str]) -> dict:
    """Accuracy, per-class F1, macro-F1."""
    out: dict = {"n": len(gold)}
    f1s = []
    for cls in labels:
        tp = sum(1 for g, p in zip(gold, pred, strict=True) if g == cls and p == cls)
        fp = sum(1 for g, p in zip(gold, pred, strict=True) if g != cls and p == cls)
        fn = sum(1 for g, p in zip(gold, pred, strict=True) if g == cls and p != cls)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        f1s.append(f1)
        out[f"f1_{cls}"] = round(f1, 3)
    out["accuracy"] = round(
        sum(1 for g, p in zip(gold, pred, strict=True) if g == p) / len(gold), 3
    )
    out["macro_f1"] = round(sum(f1s) / len(f1s), 3)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Eval aspect/saliency моделей.")
    parser.add_argument("--task", choices=["aspect_sentiment", "saliency"], required=True)
    parser.add_argument("--eval", dest="eval_path", default=None)
    parser.add_argument("--model", required=True, help="Каталог модели.")
    parser.add_argument("--save", default=None)
    args = parser.parse_args()

    eval_path = args.eval_path or f"data/{args.task}_eval.jsonl"
    records = read_jsonl(eval_path)
    if not records:
        sys.exit(f"Пустой eval: {eval_path}. Сначала build_aspect_dataset.py")
    texts = [r["text"] for r in records]
    gold = [r["label"] for r in records]
    labels = sorted(set(gold))

    results = []
    # Бейзлайны.
    if args.task == "aspect_sentiment":
        base_pred = [r.get("article_sentiment") or "neutral" for r in records]
        base = macro_metrics(gold, base_pred, labels)
        base["model"] = "baseline: копия тональности статьи (до F1)"
    else:
        major = Counter(gold).most_common(1)[0][0]
        base = macro_metrics(gold, [major] * len(gold), labels)
        base["model"] = f"baseline: всегда {major}"
    results.append(base)

    from geoanalytics.nlp._seqcls import SeqClsAdapter

    adapter = SeqClsAdapter(args.model)
    pred = [adapter.predict_label(t) for t in texts]
    m = macro_metrics(gold, pred, labels)
    m["model"] = args.model
    results.append(m)

    print(f"\nEval {args.task}: {eval_path} (n={len(gold)}, "
          f"{dict(Counter(gold))})")
    for r in results:
        per_class = "  ".join(f"{k.removeprefix('f1_')}={v}" for k, v in r.items()
                              if k.startswith("f1_"))
        print(f"  {r['model'][:60]:60} mF1={r['macro_f1']} acc={r['accuracy']}  {per_class}")
    verdict = ("МОДЕЛЬ ЛУЧШЕ бейзлайна" if m["macro_f1"] > base["macro_f1"]
               else "модель НЕ лучше бейзлайна — деплой не оправдан")
    print(f"Вердикт: {verdict} ({m['macro_f1']} vs {base['macro_f1']})")

    if args.save:
        payload = {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "task": args.task, "eval_path": eval_path, "n": len(gold),
            "gold_distribution": dict(Counter(gold)), "results": results,
        }
        Path(args.save).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"Сохранено: {args.save}")


if __name__ == "__main__":
    main()
