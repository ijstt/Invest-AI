#!/usr/bin/env python
"""Eval F5 numeric extraction: rule-based экстрактор vs золото Qwen.

Сетап: nlp/numeric.py — детерминированные правила; Qwen (llm_label_numeric.py)
даёт эталонную QA-разметку {dividend, key_rate, deal}. Сравнение per kind:
    TP — правило извлекло значение золота (отн. толерантность 0.5%, для deal —
         совпадение валюты);
    FP — правило извлекло то, чего нет в золоте (или другое число);
    FN — золото есть, правило промолчало.

Вердикт: ДЕПЛОЙ при precision ≥ 0.8 по каждому kind с ≥5 позитивами в золоте —
извлечённые числа идут в расчёты (дивдоходность), ложное число хуже пропуска.
Recall информативен: пропуск = поведение до F5.

Золото 7B шумит на deal (метит сделкой суды/бюджеты/размещения вопреки промпту)
и галлюцинирует числа из примера промпта — ручная адъюдикация хранится в
--fixes (data/numeric_gold_fixes.jsonl, поля-переопределения с reason).

Запуск:
    python scripts/eval_numeric.py [--gold data/numeric_gold.jsonl] [--save ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from geoanalytics.nlp.numeric import (  # noqa: E402
    DEAL_AMOUNT,
    DIVIDEND,
    KEY_RATE,
    extract_numbers,
)

_MULT = {"тыс": 1e3, "млн": 1e6, "млрд": 1e9, "трлн": 1e12, None: 1.0}
_REL_TOL = 0.005
_MIN_POSITIVES = 5
_PRECISION_GATE = 0.8


def _match(a: float, b: float) -> bool:
    return abs(a - b) <= _REL_TOL * max(abs(a), abs(b))


def _gold_norm(row: dict) -> dict[str, tuple[float, str] | None]:
    """Золото → {kind: (значение, unit) | None} в той же нормализации, что правила."""
    deal = row.get("deal")
    return {
        DIVIDEND: (float(row["dividend"]), "RUB") if row.get("dividend") is not None else None,
        KEY_RATE: (float(row["key_rate"]), "pct") if row.get("key_rate") is not None else None,
        DEAL_AMOUNT: (deal["value"] * _MULT[deal.get("mult")], deal["currency"])
        if deal else None,
    }


def evaluate(rows: list[dict]) -> dict:
    kinds = (DIVIDEND, KEY_RATE, DEAL_AMOUNT)
    stats = {k: {"tp": 0, "fp": 0, "fn": 0, "gold": 0} for k in kinds}
    mismatches: list[dict] = []

    for row in rows:
        gold = _gold_norm(row)
        pred: dict[str, list[tuple[float, str]]] = {k: [] for k in kinds}
        for fact in extract_numbers(row["text"]):
            pred[fact.kind].append((fact.value, fact.unit))

        for kind in kinds:
            g = gold[kind]
            if g is not None:
                stats[kind]["gold"] += 1
            hit = False
            for value, unit in pred[kind]:
                if g is not None and unit == g[1] and _match(value, g[0]):
                    hit = True
                else:
                    stats[kind]["fp"] += 1
                    mismatches.append({"article_id": row["article_id"], "kind": kind,
                                       "type": "fp", "pred": value, "unit": unit,
                                       "gold": g, "text": row["text"][:120]})
            if g is not None:
                if hit:
                    stats[kind]["tp"] += 1
                else:
                    stats[kind]["fn"] += 1
                    if not pred[kind]:
                        mismatches.append({"article_id": row["article_id"], "kind": kind,
                                           "type": "fn", "gold": g,
                                           "text": row["text"][:120]})

    report: dict = {"n": len(rows), "kinds": {}, "mismatches": mismatches}
    for kind, s in stats.items():
        precision = s["tp"] / (s["tp"] + s["fp"]) if s["tp"] + s["fp"] else None
        recall = s["tp"] / (s["tp"] + s["fn"]) if s["tp"] + s["fn"] else None
        report["kinds"][kind] = {**s, "precision": precision, "recall": recall}
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Eval numeric extraction (F5).")
    parser.add_argument("--gold", default="data/numeric_gold.jsonl")
    parser.add_argument("--fixes", default="data/numeric_gold_fixes.jsonl")
    parser.add_argument("--save", default=None)
    args = parser.parse_args()

    rows = [json.loads(line) for line in Path(args.gold).open(encoding="utf-8")
            if line.strip()]
    fixes_path = Path(args.fixes)
    if fixes_path.exists():
        fixes = {}
        for line in fixes_path.open(encoding="utf-8"):
            if line.strip():
                fx = json.loads(line)
                fixes.setdefault(fx["article_id"], {}).update(
                    {k: v for k, v in fx.items() if k in ("dividend", "key_rate", "deal")}
                )
        n_fixed = 0
        for row in rows:
            if row["article_id"] in fixes:
                row.update(fixes[row["article_id"]])
                n_fixed += 1
        print(f"Адъюдикация: применено поправок к {n_fixed} примерам ({fixes_path})")
    report = evaluate(rows)

    print(f"Примеров: {report['n']}")
    verdict_ok = True
    for kind, s in report["kinds"].items():
        prec = f"{s['precision']:.3f}" if s["precision"] is not None else "n/a"
        rec = f"{s['recall']:.3f}" if s["recall"] is not None else "n/a"
        print(f"  {kind:12s} gold={s['gold']:3d} tp={s['tp']:3d} fp={s['fp']:3d} "
              f"fn={s['fn']:3d}  precision={prec} recall={rec}")
        if s["gold"] >= _MIN_POSITIVES:
            if s["precision"] is None or s["precision"] < _PRECISION_GATE:
                verdict_ok = False
        elif s["fp"] > 0 and s["precision"] is not None \
                and s["precision"] < _PRECISION_GATE:
            verdict_ok = False

    print("\nВЕРДИКТ:", "ДЕПЛОЙ — precision ≥ 0.8 по всем kind" if verdict_ok
          else "НЕ ДЕПЛОИТЬ — есть kind с precision < 0.8, чинить правила")
    fp_fn = [m for m in report["mismatches"]][:15]
    if fp_fn:
        print("\nПримеры расхождений (до 15):")
        for m in fp_fn:
            print(f"  [{m['type']}] {m['kind']}: pred={m.get('pred')} "
                  f"gold={m.get('gold')} | {m['text']}")

    if args.save:
        out = Path(args.save)
        out.parent.mkdir(parents=True, exist_ok=True)
        report["verdict_deploy"] = verdict_ok
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                       encoding="utf-8")
        print(f"\nОтчёт сохранён: {out}")


if __name__ == "__main__":
    main()
