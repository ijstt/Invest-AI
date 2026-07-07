#!/usr/bin/env python
"""Eval ask-пути (RAG) на золотом наборе вопросов (Фаза A2 roadmap).

Зачем: ask-путь (`query/ask.py:answer`) — интент-роутер + диспатч в аналитику +
LLM-нарратив. Раньше его качество нигде не мерилось. Этот харнес гоняет фиксированный
золотой набор `data/eval/ask_golden.jsonl` и считает воспроизводимые метрики:
- intent-accuracy   - верно ли определён интент;
- ticker-accuracy   - верно ли разрешён тикер (LLM путает «роснефти»→ROSN);
- fact-recall       - доля обязательных фактов (подстрок), попавших в ответ/факты;
- language-drift     - доля ответов с остаточными CJK-символами (срыв рус↔кит);
- llm-usage          - доля ответов, реально собранных LLM (а не шаблоном-фолбэком).

Запуск:
    .venv/bin/python scripts/eval_ask.py            # полный путь с LLM (Ollama)
    .venv/bin/python scripts/eval_ask.py --no-llm   # только эвристика (быстро, без сети)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from geoanalytics.query.ask import _has_cjk, answer

GOLDEN = _ROOT / "data" / "eval" / "ask_golden.jsonl"


# --------------------------------------------------------------------------- #
# Чистые функции оценки (тестируются без БД/сети).
# --------------------------------------------------------------------------- #
def fact_recall(required: list[str], haystack: str) -> float | None:
    """Доля обязательных подстрок, найденных в тексте (регистронезависимо).

    None, если обязательных фактов нет (вопрос не проверяется на факты).
    """
    if not required:
        return None
    h = haystack.lower()
    hits = sum(1 for f in required if f.lower() in h)
    return round(hits / len(required), 3)


def evaluate_case(expected: dict, got: dict) -> dict:
    """Сравнить эталон с результатом ask. Чистая: `got` уже извлечён из AskResult."""
    intent_ok = got["intent"] == expected["intent"]
    exp_ticker = expected.get("ticker")
    ticker_ok = (got.get("ticker") == exp_ticker) if exp_ticker else None
    haystack = got["answer"] + " \n " + " \n ".join(got.get("facts", []))
    return {
        "intent_ok": intent_ok,
        "ticker_ok": ticker_ok,
        "fact_recall": fact_recall(expected.get("facts", []), haystack),
        "cjk": _has_cjk(got["answer"]),
        "used_llm": got.get("used_llm", False),
    }


def _mean(vals: list[float]) -> float | None:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def aggregate(rows: list[dict]) -> dict:
    """Сводные метрики по всем кейсам."""
    n = len(rows)
    ticker_cases = [r for r in rows if r["ticker_ok"] is not None]
    return {
        "n": n,
        "intent_accuracy": round(sum(r["intent_ok"] for r in rows) / n, 3) if n else None,
        "ticker_accuracy": (round(sum(r["ticker_ok"] for r in ticker_cases)
                                  / len(ticker_cases), 3) if ticker_cases else None),
        "ticker_cases": len(ticker_cases),
        "mean_fact_recall": _mean([r["fact_recall"] for r in rows]),
        "language_drift_rate": round(sum(r["cjk"] for r in rows) / n, 3) if n else None,
        "llm_usage_rate": round(sum(r["used_llm"] for r in rows) / n, 3) if n else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Eval ask-пути на золотом наборе.")
    ap.add_argument("--no-llm", action="store_true", help="Только эвристика (без Ollama).")
    ap.add_argument("--golden", default=str(GOLDEN))
    ap.add_argument("--out", default="data/eval/ask_baseline.json")
    ap.add_argument("--verbose", action="store_true", help="Печатать каждую ошибку.")
    args = ap.parse_args()

    use_llm = not args.no_llm
    cases = [json.loads(line) for line in Path(args.golden).read_text(encoding="utf-8").splitlines()
             if line.strip()]
    print(f"Золотой набор: {len(cases)} вопросов; режим: {'LLM' if use_llm else 'эвристика'}\n")

    rows: list[dict] = []
    for i, exp in enumerate(cases, 1):
        res = answer(exp["question"], use_llm=use_llm)
        got = {"intent": res.intent, "ticker": res.ticker, "answer": res.answer,
               "facts": res.facts, "used_llm": res.used_llm}
        row = evaluate_case(exp, got)
        row["question"] = exp["question"]
        rows.append(row)
        flags = []
        if not row["intent_ok"]:
            flags.append(f"intent={res.intent}≠{exp['intent']}")
        if row["ticker_ok"] is False:
            flags.append(f"ticker={res.ticker}≠{exp.get('ticker')}")
        if row["fact_recall"] is not None and row["fact_recall"] < 1.0:
            flags.append(f"recall={row['fact_recall']}")
        if row["cjk"]:
            flags.append("CJK")
        mark = "OK " if not flags else "!! "
        if args.verbose or flags:
            print(f"  {mark}[{i:2d}] {exp['question'][:48]:48s} {' '.join(flags)}")

    agg = aggregate(rows)
    print("\n=== СВОДКА ===")
    for k, v in agg.items():
        print(f"  {k}: {v}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"mode": "llm" if use_llm else "heuristic",
                               "aggregate": agg, "cases": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nСохранено: {out}")


if __name__ == "__main__":
    main()
