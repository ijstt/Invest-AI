#!/usr/bin/env python
"""Eval significance-моделей на РЫНОЧНОМ золоте (E3, Волна 1).

Меряет, насколько модель предсказывает фактическую реакцию рынка
(метки moved/flat из build_market_dataset.py, временной сплит — без утечки):

- модели v3 (метки moved/flat) — напрямую;
- прод-модель (метки low/medium/high) — через маппинг: high→moved (это и есть
  семантика гейта алертов 0.6: проходит только high), отдельно показывается
  и мягкий маппинг medium+high→moved;
- бейзлайн «всегда flat» (мажоритарный класс).

Запуск:
    python scripts/eval_market_significance.py \
        --models data/adapters/significance-v3-market data/adapters/significance-ft-tiny2 \
        [--eval data/significance_market_eval.jsonl] [--save data/eval/significance_market.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from geoanalytics.nlp.dataset import read_jsonl  # noqa: E402

MOVED, FLAT = "moved", "flat"
# Маппинги чужих схем меток в бинарную рыночную.
PROD_STRICT = {"high": MOVED, "medium": FLAT, "low": FLAT}     # семантика гейта 0.6
PROD_SOFT = {"high": MOVED, "medium": MOVED, "low": FLAT}      # мягкий вариант


def binary_metrics(gold: list[str], pred: list[str]) -> dict:
    """Accuracy, per-class F1 и macro-F1 для меток moved/flat."""
    out: dict = {"n": len(gold)}
    f1s = []
    for cls in (MOVED, FLAT):
        tp = sum(1 for g, p in zip(gold, pred, strict=True) if g == cls and p == cls)
        fp = sum(1 for g, p in zip(gold, pred, strict=True) if g != cls and p == cls)
        fn = sum(1 for g, p in zip(gold, pred, strict=True) if g == cls and p != cls)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        f1s.append(f1)
        out[f"f1_{cls}"] = round(f1, 3)
        out[f"precision_{cls}"] = round(prec, 3)
        out[f"recall_{cls}"] = round(rec, 3)
    out["accuracy"] = round(
        sum(1 for g, p in zip(gold, pred, strict=True) if g == p) / len(gold), 3
    )
    out["macro_f1"] = round(sum(f1s) / len(f1s), 3)
    return out


def eval_model(model_dir: str, texts: list[str], gold: list[str]) -> list[dict]:
    """Оценка одного каталога модели; чужие схемы меток — через маппинги."""
    from geoanalytics.nlp._seqcls import SeqClsAdapter

    adapter = SeqClsAdapter(model_dir)
    raw = [adapter.predict_label(t) for t in texts]
    results = []
    if set(adapter.labels) <= {MOVED, FLAT}:
        m = binary_metrics(gold, raw)
        m["model"] = model_dir
        results.append(m)
    else:  # прод-схема low/medium/high
        for name, mapping in (("strict high→moved", PROD_STRICT),
                              ("soft med+high→moved", PROD_SOFT)):
            pred = [mapping.get(label, FLAT) for label in raw]
            m = binary_metrics(gold, pred)
            m["model"] = f"{model_dir} [{name}]"
            results.append(m)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Eval significance на рыночном золоте.")
    parser.add_argument("--eval", dest="eval_path",
                        default="data/significance_market_eval.jsonl")
    parser.add_argument("--models", nargs="+", required=True,
                        help="Каталоги моделей (v3 moved/flat или прод low/medium/high).")
    parser.add_argument("--save", default=None, help="Куда сохранить JSON-отчёт.")
    args = parser.parse_args()

    records = read_jsonl(args.eval_path)
    if not records:
        sys.exit(f"Пустой eval: {args.eval_path}. Сначала build_market_dataset.py")
    texts = [r["text"] for r in records]
    gold = [r["label"] for r in records]

    results = []
    # Бейзлайн: всегда мажоритарный класс (flat).
    base = binary_metrics(gold, [FLAT] * len(gold))
    base["model"] = "baseline: always flat"
    results.append(base)
    for model_dir in args.models:
        try:
            results.extend(eval_model(model_dir, texts, gold))
        except Exception as exc:  # noqa: BLE001 — одна модель не валит сравнение
            print(f"ОШИБКА {model_dir}: {exc}")

    print(f"\nEval: {args.eval_path} (n={len(gold)}, "
          f"moved={gold.count(MOVED)}, flat={gold.count(FLAT)})")
    header = f"{'модель':58} {'mF1':>6} {'acc':>6} {'P(moved)':>9} {'R(moved)':>9}"
    print(header)
    print("-" * len(header))
    for m in results:
        print(f"{m['model'][:58]:58} {m['macro_f1']:>6} {m['accuracy']:>6} "
              f"{m['precision_moved']:>9} {m['recall_moved']:>9}")

    if args.save:
        payload = {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "eval_path": args.eval_path, "n": len(gold),
            "gold_distribution": {MOVED: gold.count(MOVED), FLAT: gold.count(FLAT)},
            "results": results,
        }
        Path(args.save).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Сохранено: {args.save}")


if __name__ == "__main__":
    main()
