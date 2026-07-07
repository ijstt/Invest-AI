"""Тесты квалити-скрина (чистая функция, без БД) + выбор полного фин.года для мультипликаторов."""

from __future__ import annotations

from geoanalytics.analytics.fundamental_factors import _last_full_year_value, quality_screen


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _stmt):
        return _FakeResult(self._rows)


def test_last_full_year_excludes_current_preliminary():
    # 2025 — текущий год (предварительный, P/E ≈99 из неполной прибыли) → берём 2024.
    rows = [("2023", 5.0), ("2024", 6.0), ("2025", 99.0)]
    assert _last_full_year_value(_FakeSession(rows), 1, "pe", current_year=2025) == 6.0


def test_last_full_year_fallback_when_only_current():
    # Завершённых лет нет — фолбэк на свежайший годовой период (лучше, чем ничего).
    rows = [("2025", 7.0)]
    assert _last_full_year_value(_FakeSession(rows), 1, "pe", current_year=2025) == 7.0


def test_last_full_year_ignores_partial_periods():
    # Полугодовые/9М-периоды не годятся для годового мультипликатора.
    rows = [("2024-H1", 5.0), ("2024-9M", 5.5)]
    assert _last_full_year_value(_FakeSession(rows), 1, "pe", current_year=2026) is None


def test_strong_company_is_ok():
    q = quality_screen({
        "net_profit": 1e11, "fcf": 5e10, "net_debt": -2e10, "ebitda": 2e11,
        "roe": 25.0, "net_margin": 22.0, "payout": 50.0,
    })
    assert q["verdict"] == "ok"
    assert q["score"] > 0.6
    assert any("ROE" in p for p in q["positives"])


def test_weak_company_is_avoid():
    q = quality_screen({
        "net_profit": -5e9, "fcf": -2e9, "net_debt": 8e11, "ebitda": 1e11,  # ND/EBITDA=8
        "roe": -4.0, "net_margin": -3.0, "payout": 150.0,
    })
    assert q["verdict"] == "avoid"
    assert q["score"] < 0.35
    assert "убыток" in q["flags"]
    assert any("высокий долг" in f for f in q["flags"])


def test_bank_without_ebitda_graceful():
    # Банк: нет revenue/ebitda/fcf — оценивается по ROE/payout, не падает.
    q = quality_screen({"net_profit": 1.7e12, "roe": 22.7, "payout": 50.0})
    assert q["verdict"] in ("ok", "caution")
    assert any("ROE" in p for p in q["positives"])


def test_falling_margin_flagged():
    base = {"net_profit": 1e10, "roe": 14.0}
    q = quality_screen(base, margin_trend=-5.0)
    assert "падающая маржа" in q["flags"]


def test_missing_metrics_neutral():
    q = quality_screen({})
    assert q["verdict"] == "caution"        # ровно нейтральные 0.5
    assert q["flags"] == [] and q["positives"] == []
