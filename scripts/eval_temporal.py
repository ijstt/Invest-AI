#!/usr/bin/env python
"""Eval F3 temporal-классификатора на временно́м hold-out (Волна 3).

Бейзлайна два — модель обязана побить оба, иначе деплой бессмыслен:
1) мажоритарный класс;
2) keyword-эвристика (маркеры будущего/прогноза в тексте) — «бесплатная»
   альтернатива модели, честная нижняя планка.

Запуск:
    python scripts/eval_temporal.py --model data/adapters/temporal-v1 \
        [--save data/eval/temporal.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from geoanalytics.nlp.dataset import read_jsonl  # noqa: E402

sys.path.insert(0, str(_ROOT / "scripts"))
from eval_aspect import macro_metrics  # noqa: E402

_FORECAST_RE = re.compile(
    r"прогноз|ожида|может\s|могут\s|вероятн|аналитик|допустил|не исключ", re.I)
_FUTURE_RE = re.compile(
    r"будет|будут|назначен|состоится|планируе|вступит|намерен|собирается|"
    r"запланирован|пройдёт|пройдет", re.I)


def keyword_baseline(text: str) -> str:
    """Эвристика по маркерам: прогнозные слова → forecast, будущее → future,
    иначе past (самый частый класс новостной ленты)."""
    if _FORECAST_RE.search(text):
        return "forecast"
    if _FUTURE_RE.search(text):
        return "future"
    return "past"


def main() -> None:
    parser = argparse.ArgumentParser(description="Eval temporal-модели.")
    parser.add_argument("--eval", dest="eval_path", default="data/temporal_eval.jsonl")
    parser.add_argument("--model", required=True, help="Каталог модели.")
    parser.add_argument("--save", default=None)
    args = parser.parse_args()

    records = read_jsonl(args.eval_path)
    if not records:
        sys.exit(f"Пустой eval: {args.eval_path}. Сначала build_temporal_dataset.py")
    texts = [r["text"] for r in records]
    gold = [r["label"] for r in records]
    labels = sorted(set(gold))

    results = []
    major = Counter(gold).most_common(1)[0][0]
    base_major = macro_metrics(gold, [major] * len(gold), labels)
    base_major["model"] = f"baseline: всегда {major}"
    results.append(base_major)

    base_kw = macro_metrics(gold, [keyword_baseline(t) for t in texts], labels)
    base_kw["model"] = "baseline: keyword-эвристика"
    results.append(base_kw)

    from geoanalytics.nlp._seqcls import SeqClsAdapter

    adapter = SeqClsAdapter(args.model)
    pred = [adapter.predict_label(t) for t in texts]
    m = macro_metrics(gold, pred, labels)
    m["model"] = args.model
    results.append(m)

    print(f"\nEval temporal: {args.eval_path} (n={len(gold)}, {dict(Counter(gold))})")
    for r in results:
        per_class = "  ".join(f"{k.removeprefix('f1_')}={v}" for k, v in r.items()
                              if k.startswith("f1_"))
        print(f"  {r['model'][:60]:60} mF1={r['macro_f1']} acc={r['accuracy']}  {per_class}")
    best_base = max(base_major["macro_f1"], base_kw["macro_f1"])
    verdict = ("МОДЕЛЬ ЛУЧШЕ обоих бейзлайнов" if m["macro_f1"] > best_base
               else "модель НЕ лучше бейзлайнов — деплой не оправдан")
    print(f"Вердикт: {verdict} ({m['macro_f1']} vs {best_base})")

    if args.save:
        payload = {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "task": "temporal", "eval_path": args.eval_path, "n": len(gold),
            "gold_distribution": dict(Counter(gold)), "results": results,
        }
        Path(args.save).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"Сохранено: {args.save}")


if __name__ == "__main__":
    main()
