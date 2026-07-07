"""Тесты чистой логики реранкинга семантического поиска (B1) — без БД/эмбеддера."""

from __future__ import annotations

from geoanalytics.query.semantic import rerank_score


def test_rerank_similarity_dominates():
    # При равных свежести/значимости выше близость → выше скор.
    hi = rerank_score(0.9, 0.5, 24)
    lo = rerank_score(0.6, 0.5, 24)
    assert hi > lo


def test_rerank_recency_breaks_ties():
    # Одинаковая близость и значимость, но свежее → выше.
    fresh = rerank_score(0.8, 0.5, 1)
    old = rerank_score(0.8, 0.5, 1000)
    assert fresh > old


def test_rerank_significance_boost():
    more_sig = rerank_score(0.8, 0.9, 24)
    less_sig = rerank_score(0.8, 0.1, 24)
    assert more_sig > less_sig


def test_rerank_handles_missing_values():
    # None значимость/возраст не должны ронять расчёт; близость остаётся главной.
    s = rerank_score(0.8, None, None)
    assert 0.0 <= s <= 1.0
    # Чистая близость 0.8 при нейтральных бустах ≈ 0.7*0.8 = 0.56.
    assert abs(s - 0.7 * 0.8) < 1e-6


def test_rerank_bounded():
    # Максимум всех компонент → не превышает 1.0.
    assert rerank_score(1.0, 1.0, 0.0) <= 1.0
    assert rerank_score(0.0, 0.0, 1e9) >= 0.0
