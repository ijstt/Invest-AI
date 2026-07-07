#!/usr/bin/env python
"""Валидация значимости: дистиллированная модель vs формула против учителя (Фаза A3).

Зачем: значимость - gate алертов и TTL-ретеншна, но качество дискретной модели (Ф7,
бакеты low/medium/high) против эталона нигде не мерилось. Этот харнес:
1. (--build) берёт случайную выборку статей и размечает их УЧИТЕЛЕМ Qwen (тот же промпт,
   что в `llm_label.py`, задача significance) → `data/eval/significance_eval.jsonl`;
2. (eval) для каждой статьи считает бакет модели (`predict_significance`) и бакет формулы
   (`significance_score` из компонентов event_type/sentiment/links) и сравнивает с учителем:
   per-bucket precision/recall, macro-F1, accuracy, confusion;
3. анализирует gate 0.6: какая доля «high» учителя проходит gate под моделью (recall high)
   и какая доля не-high ошибочно проходит (ложные алерты).

ВАЖНО про утечку: дистиллированная модель обучалась на метках Qwen по ЭТОМУ корпусу,
поэтому сравнение модель↔учитель ближе к «верности учителю на корпусе», чем к обобщению.
Сравнение ФОРМУЛА↔учитель свободно от утечки (формула не обучалась на метках) и потому -
главный честный сигнал «насколько дешёвая формула отстаёт от учителя».

Запуск:
    .venv/bin/python scripts/eval_significance.py --build 160   # разметка учителем (долго)
    .venv/bin/python scripts/eval_significance.py               # метрики по готовому файлу
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from sqlalchemy import func, select

from config.settings import get_settings
from geoanalytics.nlp.significance import (
    SIG_BUCKETS,
    significance_bucket,
    significance_score,
)
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import Article, ArticleEntity

EVAL_PATH = _ROOT / "data" / "eval" / "significance_eval.jsonl"


def _load_llm_label():
    """Грузит scripts/llm_label.py как модуль (переиспользуем промпт/парсер учителя)."""
    spec = importlib.util.spec_from_file_location("llm_label", _ROOT / "scripts" / "llm_label.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Чистые метрики (тестируются без БД/сети).
# --------------------------------------------------------------------------- #
def per_class_prf(pairs: list[tuple[str, str]], classes=SIG_BUCKETS) -> dict:
    """pairs = [(gold, pred)]. Возвращает per-class precision/recall/f1 + macro-F1 + accuracy."""
    tp = defaultdict(int)
    fp = defaultdict(int)
    fn = defaultdict(int)
    correct = 0
    for gold, pred in pairs:
        if gold == pred:
            tp[gold] += 1
            correct += 1
        else:
            fp[pred] += 1
            fn[gold] += 1
    out: dict = {"per_class": {}}
    f1s = []
    for c in classes:
        p = tp[c] / (tp[c] + fp[c]) if (tp[c] + fp[c]) else 0.0
        r = tp[c] / (tp[c] + fn[c]) if (tp[c] + fn[c]) else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        out["per_class"][c] = {"precision": round(p, 3), "recall": round(r, 3),
                               "f1": round(f1, 3), "support": tp[c] + fn[c]}
        f1s.append(f1)
    out["macro_f1"] = round(sum(f1s) / len(f1s), 3) if f1s else 0.0
    out["accuracy"] = round(correct / len(pairs), 3) if pairs else 0.0
    return out


def confusion(pairs: list[tuple[str, str]], classes=SIG_BUCKETS) -> dict:
    """Матрица ошибок gold→pred (вложенный dict)."""
    m = {g: {p: 0 for p in classes} for g in classes}
    for gold, pred in pairs:
        if gold in m and pred in m[gold]:
            m[gold][pred] += 1
    return m


def gate_analysis(pairs_value: list[tuple[str, float]], gate: float) -> dict:
    """Для (gold_bucket, model_value): recall high и доля ложных проходов gate среди не-high."""
    high_total = sum(1 for g, _ in pairs_value if g == "high")
    high_pass = sum(1 for g, v in pairs_value if g == "high" and v >= gate)
    nonhigh_total = sum(1 for g, _ in pairs_value if g != "high")
    nonhigh_pass = sum(1 for g, v in pairs_value if g != "high" and v >= gate)
    return {
        "gate": gate,
        "high_recall": round(high_pass / high_total, 3) if high_total else None,
        "high_total": high_total,
        "false_pass_rate": round(nonhigh_pass / nonhigh_total, 3) if nonhigh_total else None,
        "nonhigh_passing": nonhigh_pass,
    }


# --------------------------------------------------------------------------- #
# Разметка выборки учителем (--build).
# --------------------------------------------------------------------------- #
def _load_exclude_norm(exclude_path: str | None) -> set[str]:
    """Множество нормализованных текстов обучающего золота — чтобы чистый hold-out с ним
    НЕ пересекался (иначе модель меряет «верность учителю на знакомых данных», а не
    обобщение — это и есть утечка A3)."""
    from geoanalytics.nlp.text import normalized_text
    if not exclude_path:
        return set()
    out: set[str] = set()
    for line in Path(exclude_path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.add(normalized_text(json.loads(line)["text"]))
    print(f"Исключаем обучающее золото: {len(out)} норм-текстов из {exclude_path}")
    return out


def build(n: int, eval_path: Path, exclude_path: str | None = None) -> None:
    from geoanalytics.nlp.dataset import _row_text
    from geoanalytics.nlp.text import normalized_text

    settings = get_settings()
    llm = _load_llm_label()
    eval_path.parent.mkdir(parents=True, exist_ok=True)
    exclude_norm = _load_exclude_norm(exclude_path)

    done_ids: set[int] = set()
    if eval_path.exists():
        for line in eval_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done_ids.add(json.loads(line)["article_id"])

    with session_scope() as s:
        ids = [r[0] for r in s.execute(
            select(Article.id).where(Article.text.isnot(None)).order_by(func.random())
        ).all()]
    random.seed(42)
    candidates = [i for i in ids if i not in done_ids]
    target = max(0, n - len(done_ids))
    print(f"Уже размечено: {len(done_ids)}; цель {n} → добрать {target}; "
          f"кандидатов {len(candidates)}")

    written = 0
    skipped_train = 0
    t0 = time.time()
    with eval_path.open("a", encoding="utf-8") as f:
        for aid in candidates:
            if written >= target:
                break
            with session_scope() as s:
                art = s.get(Article, aid)
                text = _row_text({"title": art.title, "text": art.text})
                etype = art.event_type
                sent = art.sentiment_score
                rels = [r for (r,) in s.execute(
                    select(ArticleEntity.relevance).where(ArticleEntity.article_id == aid)).all()
                    if r is not None]
            if not text:
                continue
            if exclude_norm and normalized_text(text) in exclude_norm:
                skipped_train += 1
                continue  # статья из обучающего золота — в чистый hold-out не берём
            gold = llm._label_one(text, "significance", settings, max_chars=600)
            if gold is None:
                continue
            f.write(json.dumps({"article_id": aid, "text": text[:600], "gold": gold,
                                "event_type": etype, "sentiment_score": sent,
                                "relevances": rels}, ensure_ascii=False) + "\n")
            f.flush()
            written += 1
            if written % 20 == 0:
                print(f"  {written}/{target} ({written / (time.time() - t0):.2f}/с)")
    print(f"Готово: {eval_path}; добавлено {written}, пропущено (в обучении) {skipped_train}")


# --------------------------------------------------------------------------- #
# Оценка.
# --------------------------------------------------------------------------- #
def _value_to_bucket(v: float) -> str:
    """Значение модели (0.15/0.5/0.85) → ближайший бакет."""
    return min(_BUCKET_VALUE, key=lambda b: abs(_BUCKET_VALUE[b] - v))


_BUCKET_VALUE = {"low": 0.15, "medium": 0.5, "high": 0.85}


def evaluate(out_path: str, gate: float, eval_path: Path) -> None:
    from geoanalytics.nlp.significance import _get_model

    records = [json.loads(line) for line in eval_path.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    if not records:
        sys.exit(f"Нет данных: {eval_path}. Сначала: --build N")
    print(f"Эталон (учитель Qwen): {len(records)} статей\n")

    model = _get_model()
    model_pairs: list[tuple[str, str]] = []
    model_value_pairs: list[tuple[str, float]] = []
    formula_pairs: list[tuple[str, str]] = []
    for r in records:
        gold = r["gold"]
        fval = significance_score(r.get("event_type"), r.get("sentiment_score"),
                                  r.get("relevances") or [])
        formula_pairs.append((gold, significance_bucket(fval)))
        if model is not None:
            try:
                mb = model.predict_label(r["text"])
            except Exception:  # noqa: BLE001
                continue
            model_pairs.append((gold, mb))
            model_value_pairs.append((gold, _BUCKET_VALUE.get(mb, 0.5)))

    result = {"n": len(records), "gate": gate}
    print("=== ФОРМУЛА vs учитель (без утечки) ===")
    fm = per_class_prf(formula_pairs)
    _print_prf(fm)
    result["formula"] = {"metrics": fm, "confusion": confusion(formula_pairs)}

    if model_pairs:
        print("\n=== МОДЕЛЬ vs учитель (есть утечка - см. докстринг) ===")
        mm = per_class_prf(model_pairs)
        _print_prf(mm)
        ga = gate_analysis(model_value_pairs, gate)
        print(f"\n=== GATE {gate} (модель) ===")
        print(f"  high recall: {ga['high_recall']} (из {ga['high_total']} high)")
        print(f"  ложный проход не-high: {ga['false_pass_rate']} ({ga['nonhigh_passing']} шт)")
        result["model"] = {"metrics": mm, "confusion": confusion(model_pairs), "gate": ga}
    else:
        print("\n(Модель значимости не загружена — оценка только формулы.)")

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nСохранено: {out}")


def _print_prf(m: dict) -> None:
    print(f"  macro-F1={m['macro_f1']} accuracy={m['accuracy']}")
    for c, v in m["per_class"].items():
        print(f"    {c:7s}: P={v['precision']} R={v['recall']} F1={v['f1']} (n={v['support']})")


def main() -> None:
    ap = argparse.ArgumentParser(description="Валидация значимости (Ф7) против учителя.")
    ap.add_argument("--build", type=int, metavar="N", help="Разметить N статей учителем Qwen.")
    ap.add_argument("--gate", type=float, default=None, help="Gate алертов (деф. из настроек).")
    ap.add_argument("--out", default="data/eval/significance_baseline.json")
    ap.add_argument("--eval-file", default=str(EVAL_PATH),
                    help="JSONL с размеченным учителем hold-out (build пишет, evaluate читает).")
    ap.add_argument("--exclude-train", default=None, metavar="GOLD.jsonl",
                    help="Исключить из hold-out статьи обучающего золота (чистый hold-out, "
                         "фикс утечки A3). Напр. data/significance_gold.jsonl")
    args = ap.parse_args()

    eval_path = Path(args.eval_file)
    if args.build:
        build(args.build, eval_path, exclude_path=args.exclude_train)
        return
    gate = args.gate if args.gate is not None else get_settings().alert_min_significance
    evaluate(args.out, gate, eval_path)


if __name__ == "__main__":
    main()
