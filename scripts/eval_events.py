#!/usr/bin/env python
"""Валидация классификатора событий (Б12): дистиллят и правила против учителя Qwen.

Закрывает мину Б12: для events не было eval-харнеса (цифры v3 жили в комментариях `.env`,
v4 лежал в `data/adapters/` и формально не сравнивался). Этот харнес — аналог
`eval_significance.py`:
1. (--build) случайная выборка статей, размеченная УЧИТЕЛЕМ Qwen (task=events), с
   исключением обучающего золота → `data/eval/events_eval.jsonl` (чистый hold-out);
2. (eval) per-class precision/recall/F1, macro-F1, accuracy, confusion для:
   - ПРАВИЛ (`_classify_by_rules`) — без утечки (правила не обучались на метках);
   - МОДЕЛИ (деплой-адаптер или указанный `--adapter` — для сравнения v3 vs v4).

ВАЖНО про утечку: дистиллят обучался на метках Qwen по этому корпусу → «модель vs учитель»
ближе к верности учителю, чем к обобщению. «Правила vs учитель» утечки не имеют. Сравнение
v3↔v4 на ОДНОМ hold-out корректно (обе меряются против одного эталона).

Запуск:
    .venv/bin/python scripts/eval_events.py --build 120 \
        --exclude-train data/events_gold.jsonl            # разметка учителем (долго)
    .venv/bin/python scripts/eval_events.py               # деплой-модель + правила
    .venv/bin/python scripts/eval_events.py --adapter data/adapters/events-ft-tiny2-v4
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
from geoanalytics.core.types import EventType
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import Article

EVAL_PATH = _ROOT / "data" / "eval" / "events_eval.jsonl"
EVENT_CLASSES = [e.value for e in EventType]


def _load_llm_label():
    """Грузит scripts/llm_label.py как модуль (переиспользуем промпт/парсер учителя)."""
    spec = importlib.util.spec_from_file_location("llm_label", _ROOT / "scripts" / "llm_label.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Чистые метрики (тестируются без БД/сети).
# --------------------------------------------------------------------------- #
def per_class_prf(pairs: list[tuple[str, str]], classes=tuple(EVENT_CLASSES)) -> dict:
    """pairs = [(gold, pred)]. Per-class precision/recall/f1 + macro-F1 + accuracy."""
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
        support = tp[c] + fn[c]
        if support == 0 and (tp[c] + fp[c]) == 0:
            continue  # класс не встретился ни в gold, ни в pred — не разбавляем macro-F1
        p = tp[c] / (tp[c] + fp[c]) if (tp[c] + fp[c]) else 0.0
        r = tp[c] / (tp[c] + fn[c]) if (tp[c] + fn[c]) else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        out["per_class"][c] = {"precision": round(p, 3), "recall": round(r, 3),
                               "f1": round(f1, 3), "support": support}
        f1s.append(f1)
    out["macro_f1"] = round(sum(f1s) / len(f1s), 3) if f1s else 0.0
    out["accuracy"] = round(correct / len(pairs), 3) if pairs else 0.0
    return out


def confusion(pairs: list[tuple[str, str]], classes=tuple(EVENT_CLASSES)) -> dict:
    """Матрица ошибок gold→pred (вложенный dict)."""
    m = {g: {p: 0 for p in classes} for g in classes}
    for gold, pred in pairs:
        if gold in m and pred in m[gold]:
            m[gold][pred] += 1
    return m


# --------------------------------------------------------------------------- #
# Разметка выборки учителем (--build).
# --------------------------------------------------------------------------- #
def _load_exclude_norm(exclude_path: str | None) -> set[str]:
    from geoanalytics.nlp.text import normalized_text
    if not exclude_path:
        return set()
    out: set[str] = set()
    for line in Path(exclude_path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            txt = rec.get("text") or rec.get("input") or ""
            if txt:
                out.add(normalized_text(txt))
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

    written = skipped_train = 0
    t0 = time.time()
    with eval_path.open("a", encoding="utf-8") as f:
        for aid in candidates:
            if written >= target:
                break
            with session_scope() as s:
                art = s.get(Article, aid)
                text = _row_text({"title": art.title, "text": art.text})
            if not text:
                continue
            if exclude_norm and normalized_text(text) in exclude_norm:
                skipped_train += 1
                continue
            gold = llm._label_one(text, "events", settings, max_chars=600)
            if gold is None:
                continue
            f.write(json.dumps({"article_id": aid, "text": text[:600], "gold": gold},
                               ensure_ascii=False) + "\n")
            f.flush()
            written += 1
            if written % 20 == 0:
                print(f"  {written}/{target} ({written / (time.time() - t0):.2f}/с)")
    print(f"Готово: {eval_path}; добавлено {written}, пропущено (в обучении) {skipped_train}")


# --------------------------------------------------------------------------- #
# Оценка.
# --------------------------------------------------------------------------- #
def _load_adapter(adapter_path: str | None):
    """Адаптер для оценки: указанный путь (сравнение версий) или деплой-классификатор."""
    if adapter_path:
        from geoanalytics.nlp._seqcls import SeqClsAdapter
        return SeqClsAdapter(adapter_path)
    from geoanalytics.nlp.classify import _get_classifier
    return _get_classifier()


def evaluate(out_path: str, eval_path: Path, adapter_path: str | None = None) -> None:
    from geoanalytics.nlp.classify import _classify_by_rules

    if not eval_path.exists():
        sys.exit(f"Нет данных: {eval_path}. Сначала: "
                 "--build N --exclude-train data/events_gold.jsonl")
    records = [json.loads(line) for line in eval_path.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    if not records:
        sys.exit(f"Нет данных: {eval_path}. Сначала: --build N")
    print(f"Эталон (учитель Qwen): {len(records)} статей\n")

    rule_pairs = [(r["gold"], _classify_by_rules(r["text"]).value) for r in records]
    print("=== ПРАВИЛА vs учитель (без утечки) ===")
    rm = per_class_prf(rule_pairs)
    _print_prf(rm)
    result = {"n": len(records), "rules": {"metrics": rm}}

    model = _load_adapter(adapter_path)
    if model is not None:
        model_pairs = []
        for r in records:
            try:
                model_pairs.append((r["gold"], model.predict_label(r["text"])))
            except Exception:  # noqa: BLE001
                continue
        tag = adapter_path or "деплой-адаптер"
        print(f"\n=== МОДЕЛЬ ({tag}) vs учитель (есть утечка — см. докстринг) ===")
        mm = per_class_prf(model_pairs)
        _print_prf(mm)
        result["model"] = {"adapter": tag, "metrics": mm, "confusion": confusion(model_pairs)}
    else:
        print("\n(Модель событий не загружена — оценка только правил.)")

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nСохранено: {out}")


def _print_prf(m: dict) -> None:
    print(f"  macro-F1={m['macro_f1']} accuracy={m['accuracy']}")
    for c, v in m["per_class"].items():
        print(f"    {c:11s}: P={v['precision']} R={v['recall']} F1={v['f1']} (n={v['support']})")


def main() -> None:
    ap = argparse.ArgumentParser(description="Валидация классификатора событий (Б12).")
    ap.add_argument("--build", type=int, metavar="N", help="Разметить N статей учителем Qwen.")
    ap.add_argument("--out", default="data/eval/events_baseline.json")
    ap.add_argument("--eval-file", default=str(EVAL_PATH))
    ap.add_argument("--exclude-train", default=None, metavar="GOLD.jsonl",
                    help="Исключить из hold-out обучающее золото (чистый hold-out). "
                         "Напр. data/events_gold.jsonl")
    ap.add_argument("--adapter", default=None, metavar="PATH",
                    help="Оценить конкретный адаптер (сравнение версий v3/v4) вместо деплоя.")
    args = ap.parse_args()

    eval_path = Path(args.eval_file)
    if args.build:
        build(args.build, eval_path, exclude_path=args.exclude_train)
        return
    evaluate(args.out, eval_path, adapter_path=args.adapter)


if __name__ == "__main__":
    main()
