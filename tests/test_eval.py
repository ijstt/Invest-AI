"""Тесты чистой логики eval-харнесов (Фаза A): метрики/парсеры без БД, сети и LLM.

Скрипты лежат в scripts/ (не в sys.path) — грузим их как модули по пути, как в
test_distillation. Проверяем именно арифметику метрик: forward-return, hit-логику,
сводки precision, fact-recall, per-class P/R/F1, gate-анализ значимости."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    # Регистрируем в sys.modules ДО exec: dataclasses при `from __future__ import
    # annotations` резолвят cls.__module__ через sys.modules (иначе AttributeNone).
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# eval_alerts
# --------------------------------------------------------------------------- #
def test_forward_return_basic():
    ea = _load("eval_alerts")
    closes = [(date(2026, 6, 1), 100.0), (date(2026, 6, 2), 102.0),
              (date(2026, 6, 4), 110.0)]
    # От 1 июня (100) через 3 дня (последняя ≤ 4 июня = 110) → +10%.
    assert ea.forward_return(closes, date(2026, 6, 1), 3) == 100.0 * 0.1
    # Нет данных вперёд (asof = последняя дата, выход не позже входа) → None.
    assert ea.forward_return(closes, date(2026, 6, 4), 3) is None
    # Пустой ряд → None.
    assert ea.forward_return([], date(2026, 6, 1), 3) is None


def test_evaluate_hit_by_type():
    ea = _load("eval_alerts")
    # neg_spike: попадание, если цена упала на ≥ порога.
    assert ea.evaluate_hit("neg_spike", -1, -3.0, 2.0) is True
    assert ea.evaluate_hit("neg_spike", -1, -1.0, 2.0) is False
    # new_event: любое движение ≥ порога по модулю.
    assert ea.evaluate_hit("new_event", 0, 2.5, 2.0) is True
    assert ea.evaluate_hit("new_event", 0, -2.5, 2.0) is True
    # price_move: персистентность (та же сторона) и достаточная величина.
    assert ea.evaluate_hit("price_move", 1, 3.0, 2.0) is True
    assert ea.evaluate_hit("price_move", 1, -3.0, 2.0) is False  # развернулось
    # Нет форвардных данных → не оценивается.
    assert ea.evaluate_hit("neg_spike", -1, None, 2.0) is None


def test_summarize_precision():
    ea = _load("eval_alerts")
    records = [
        ("neg_spike", "warning", True),
        ("neg_spike", "warning", False),
        ("new_event", "critical", None),   # не оценён → не входит в precision
        ("new_event", "critical", True),
    ]
    out = ea.summarize(records, days=2)
    assert out["overall"]["total"] == 4
    assert out["overall"]["scored"] == 3
    assert out["overall"]["hits"] == 2
    assert out["overall"]["precision"] == round(2 / 3, 3)
    assert out["by_type"]["neg_spike"]["precision"] == 0.5
    assert out["by_type"]["new_event"]["precision"] == 1.0  # 1 hit из 1 оценённого


# --------------------------------------------------------------------------- #
# eval_ask
# --------------------------------------------------------------------------- #
def test_fact_recall():
    eq = _load("eval_ask")
    assert eq.fact_recall([], "что угодно") is None
    assert eq.fact_recall(["SBER"], "Сбербанк (SBER) растёт") == 1.0
    assert eq.fact_recall(["SBER", "RSI"], "только SBER тут") == 0.5
    assert eq.fact_recall(["банки"], "Сектор «Банки» РФ") == 1.0  # регистр игнорируется


def test_evaluate_case_and_aggregate():
    eq = _load("eval_ask")
    exp = {"intent": "asset", "ticker": "SBER", "facts": ["SBER"]}
    got = {"intent": "asset", "ticker": "SBER", "answer": "SBER ок", "facts": [],
           "used_llm": True}
    row = eq.evaluate_case(exp, got)
    assert row["intent_ok"] is True
    assert row["ticker_ok"] is True
    assert row["fact_recall"] == 1.0
    assert row["cjk"] is False
    # Кейс без ожидаемого тикера → ticker_ok = None (не учитывается в accuracy).
    exp2 = {"intent": "market", "ticker": None, "facts": []}
    got2 = {"intent": "news", "ticker": None, "answer": "...", "facts": [], "used_llm": False}
    row2 = eq.evaluate_case(exp2, got2)
    assert row2["ticker_ok"] is None
    assert row2["intent_ok"] is False
    agg = eq.aggregate([row, row2])
    assert agg["n"] == 2
    assert agg["intent_accuracy"] == 0.5
    assert agg["ticker_accuracy"] == 1.0   # один тикер-кейс, верный
    assert agg["ticker_cases"] == 1


def test_cjk_detection_via_evaluate_case():
    eq = _load("eval_ask")
    got = {"intent": "asset", "ticker": None, "answer": "ответ 中文", "facts": [],
           "used_llm": True}
    row = eq.evaluate_case({"intent": "asset", "facts": []}, got)
    assert row["cjk"] is True


# --------------------------------------------------------------------------- #
# eval_significance
# --------------------------------------------------------------------------- #
def test_per_class_prf_perfect_and_mixed():
    es = _load("eval_significance")
    pairs = [("high", "high"), ("low", "low"), ("medium", "medium")]
    m = es.per_class_prf(pairs)
    assert m["accuracy"] == 1.0
    assert m["macro_f1"] == 1.0
    # Одна ошибка: high предсказан как medium.
    pairs2 = [("high", "medium"), ("high", "high"), ("low", "low")]
    m2 = es.per_class_prf(pairs2)
    assert m2["accuracy"] == round(2 / 3, 3)
    assert m2["per_class"]["high"]["recall"] == 0.5


def test_confusion_counts():
    es = _load("eval_significance")
    pairs = [("high", "medium"), ("high", "high"), ("low", "low")]
    cm = es.confusion(pairs)
    assert cm["high"]["medium"] == 1
    assert cm["high"]["high"] == 1
    assert cm["low"]["low"] == 1


def test_gate_analysis():
    es = _load("eval_significance")
    # gold/значение модели: 2 high (одно проходит gate 0.6), 2 не-high (одно ложно проходит).
    pairs = [("high", 0.85), ("high", 0.5), ("medium", 0.5), ("low", 0.85)]
    ga = es.gate_analysis(pairs, 0.6)
    assert ga["high_recall"] == 0.5
    assert ga["high_total"] == 2
    assert ga["false_pass_rate"] == 0.5
    assert ga["nonhigh_passing"] == 1


def test_value_to_bucket():
    es = _load("eval_significance")
    assert es._value_to_bucket(0.85) == "high"
    assert es._value_to_bucket(0.15) == "low"
    assert es._value_to_bucket(0.5) == "medium"
    assert es._value_to_bucket(0.6) == "medium"  # 0.6: |0.6-0.5|=0.10 < |0.6-0.85|=0.25


# --------------------------------------------------------------------------- #
# eval_events (Б12)
# --------------------------------------------------------------------------- #
def test_events_per_class_prf_skips_absent_classes():
    ee = _load("eval_events")
    pairs = [("sanctions", "sanctions"), ("macro", "macro"), ("macro", "other")]
    m = ee.per_class_prf(pairs)
    assert m["accuracy"] == round(2 / 3, 3)
    # Класс sanctions идеален; macro: recall 1/2.
    assert m["per_class"]["sanctions"]["f1"] == 1.0
    assert m["per_class"]["macro"]["recall"] == 0.5
    # Невстретившиеся классы (напр. dividends) не разбавляют macro-F1.
    assert "dividends" not in m["per_class"]


def test_events_confusion_counts():
    ee = _load("eval_events")
    cm = ee.confusion([("macro", "other"), ("macro", "macro")])
    assert cm["macro"]["other"] == 1
    assert cm["macro"]["macro"] == 1
