"""Тесты H5: rule-based извлечение фундаментальных метрик из текста отчётов (чистое ядро)."""

from __future__ import annotations

from geoanalytics.analytics.fundamentals import format_value
from geoanalytics.nlp.fundamentals import detect_period, extract_fundamentals

_REPORT = (
    "Финансовые результаты за 2024 год. Выручка составила 7 200 млрд руб., "
    "увеличившись на 12%. Чистая прибыль достигла 1 508 млрд руб. "
    "EBITDA выросла до 3 500 млрд руб. Совокупные активы 52 000 млрд руб. "
    "Собственный капитал составил 6 800 млрд руб. "
    "Дивиденд рекомендован в размере 34,84 руб. на акцию. P/E составил 5,2."
)


def test_extract_core_metrics_scaled():
    facts = {f.metric: f for f in extract_fundamentals(_REPORT)}
    assert facts["revenue"].value == 7_200e9 and facts["revenue"].unit == "RUB"
    assert facts["net_profit"].value == 1_508e9
    assert facts["ebitda"].value == 3_500e9
    assert facts["assets"].value == 52_000e9
    assert facts["equity"].value == 6_800e9
    # период проставлен из текста на каждый факт
    assert facts["revenue"].period == "2024"


def test_extract_per_share_and_ratio():
    facts = {f.metric: f for f in extract_fundamentals(_REPORT)}
    assert facts["dividend"].value == 34.84 and facts["dividend"].unit == "RUB"
    assert facts["pe"].value == 5.2 and facts["pe"].unit == "ratio"


def test_precision_first_no_unit_skipped():
    """Число без узнаваемой единицы/метки не извлекается (precision-first)."""
    facts = extract_fundamentals("Выручка выросла. Прибыль хорошая. Рост 12%.")
    assert facts == []


def test_detect_period_qualifiers():
    assert detect_period("Отчёт за 1 полугодие 2024 года") == "2024-H1"
    assert detect_period("За 9 месяцев 2023 года выручка") == "2023-9M"
    assert detect_period("Результаты 2025") == "2025"
    assert detect_period("без года") is None


def test_period_override():
    facts = extract_fundamentals("Выручка 100 млрд руб.", period="2024-Q3")
    assert facts[0].period == "2024-Q3"


def test_format_value_scales():
    assert format_value("revenue", 7_200e9, "RUB") == "7.20 трлн ₽"
    assert format_value("ebitda", 350e9, "RUB") == "350.0 млрд ₽"
    assert format_value("net_profit", 4.5e6, "RUB") == "4.5 млн ₽"
    assert format_value("dividend", 34.84, "RUB") == "34.84 ₽"
    assert format_value("pe", 5.2, "ratio") == "5.20"
