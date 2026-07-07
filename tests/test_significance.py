"""Тесты значимости новости (M6): чистая функция, без БД."""

from __future__ import annotations

from types import SimpleNamespace

from geoanalytics.core.types import EventType
from geoanalytics.nlp.significance import (
    significance_gates,
    significance_score,
    type_weight,
    validate_cascade,
)


def _settings(ingest: float, alert: float) -> SimpleNamespace:
    return SimpleNamespace(min_significance=ingest, alert_min_significance=alert)


def test_significance_gates_reads_settings():
    assert significance_gates(_settings(0.2, 0.35)) == {"ingest": 0.2, "alert": 0.35}


def test_validate_cascade_ok_for_monotonic():
    # Б6: инжест ≤ алерт. Проверка выравнивания с бакетами — только при активной модели,
    # поэтому без модели здесь чистая монотонность.
    assert validate_cascade(_settings(0.2, 0.35)) == []


def test_validate_cascade_flags_inverted_gates():
    problems = validate_cascade(_settings(0.5, 0.3))
    assert any("недосчитыва" in p for p in problems)


def test_validate_cascade_flags_out_of_range():
    assert validate_cascade(_settings(-0.1, 1.5))


def test_type_weight_known_and_unknown():
    assert type_weight(EventType.SANCTIONS.value) == 1.0
    assert type_weight(EventType.OTHER.value) == 0.1
    assert type_weight(EventType.NOISE.value) == 0.02   # шум почти не значим
    # неизвестная/пустая категория трактуется как OTHER
    assert type_weight(None) == 0.1
    assert type_weight("nonsense") == 0.1


def test_significance_in_unit_range():
    s = significance_score(EventType.SANCTIONS.value, -1.0, [1.0, 1.0])
    assert 0.0 <= s <= 1.0
    assert s == 1.0  # максимум по всем слагаемым (тип=1, |тон|=1, связи=1)


def test_significance_monotonic_in_type():
    base = dict(sentiment_score=0.5, link_relevances=[0.7])
    high = significance_score(EventType.SANCTIONS.value, **base)
    low = significance_score(EventType.OTHER.value, **base)
    assert high > low


def test_significance_links_saturate():
    # сумма релевантностей > 1 не повышает фактор связей сверх 1.0
    a = significance_score(EventType.MACRO.value, 0.0, [1.0])
    b = significance_score(EventType.MACRO.value, 0.0, [0.7, 0.7, 0.7])
    assert a == b


def test_significance_no_links_no_sentiment():
    # только вклад типа: OTHER с w_type=0.5 → 0.05 (OTHER понижен до 0.1)
    s = significance_score(EventType.OTHER.value, None, None)
    assert s == round(0.5 * 0.1, 3)


def test_significance_custom_weights():
    s = significance_score(EventType.OTHER.value, 1.0, [1.0],
                           w_type=0.0, w_sent=1.0, w_link=0.0)
    assert s == 1.0
