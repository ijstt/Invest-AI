"""Тесты L2 (состав компании): профиль эмитента, сегменты выручки, композиция для карточки.

В проекте нет БД-фикстур (см. test_repositories) — используем фейк-сессии и конструируем
ORM-объекты без записи в БД (доступ к атрибутам не требует сессии).
"""

from __future__ import annotations

from geoanalytics.analytics.fundamentals import (
    _latest_fact,
    composition_for_asset,
    update_company_profile,
)
from geoanalytics.nlp.fundamentals import FundamentalFact
from geoanalytics.storage.models import Asset, Company, Price, RevenueSegment
from geoanalytics.storage.repositories import RevenueSegmentRepository


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Минимальная фейк-сессия: scalars() для репозиториев + get()/flush() для профиля."""

    def __init__(self, rows=None, company=None):
        self._rows = rows or []
        self._company = company

    def scalars(self, _stmt):
        return _FakeScalars(self._rows)

    def get(self, _model, _pk):
        return self._company

    def flush(self):
        pass


# --------------------------------------------------------------------------- #
# _latest_fact
# --------------------------------------------------------------------------- #
def test_latest_fact_picks_newest_period():
    facts = [
        FundamentalFact("market_cap", 100.0, "RUB", "2022", "x"),
        FundamentalFact("market_cap", 300.0, "RUB", "2024", "x"),
        FundamentalFact("revenue", 5.0, "RUB", "2024", "x"),
    ]
    assert _latest_fact(facts, "market_cap").value == 300.0
    assert _latest_fact(facts, "free_float") is None


# --------------------------------------------------------------------------- #
# RevenueSegmentRepository.for_company — фильтр свежайшего периода + сорт по доле
# --------------------------------------------------------------------------- #
def test_for_company_filters_latest_period_and_sorts_by_share():
    # Репозиторий полагается на SQL order_by(period desc) → фейк отдаёт уже период-убыв.
    rows = [
        RevenueSegment(company_id=1, segment="A", value=10.0, share=20.0, period="2024"),
        RevenueSegment(company_id=1, segment="B", value=40.0, share=70.0, period="2024"),
        RevenueSegment(company_id=1, segment="Old", value=99.0, share=99.0, period="2023"),
    ]
    out = RevenueSegmentRepository(_FakeSession(rows)).for_company(1)
    # только 2024, отсортировано по доле убыв.; прошлый период отброшен
    assert [s.segment for s in out] == ["B", "A"]


def test_for_company_empty():
    assert RevenueSegmentRepository(_FakeSession([])).for_company(1) == []


# --------------------------------------------------------------------------- #
# composition_for_asset
# --------------------------------------------------------------------------- #
def test_composition_profile_and_segments():
    company = Company(id=1, name="АФК", description="Холдинг",
                      market_cap=3.0e11, free_float=30.0, shares=9.6e9)
    asset = Asset(ticker="AFKS", name="АФК", company_id=1)
    asset.company = company
    rows = [RevenueSegment(company_id=1, segment="Связь", value=5.0e11,
                           share=60.0, period="2024")]
    comp = composition_for_asset(_FakeSession(rows), asset)
    assert comp is not None
    assert comp["profile"]["free_float"] == 30.0
    assert comp["profile"]["market_cap_display"]  # «300.0 млрд ₽» и т.п.
    assert comp["segments"][0]["segment"] == "Связь"
    assert comp["segments"][0]["share"] == 60.0


def test_composition_none_without_company():
    asset = Asset(ticker="X", name="x", company_id=None)
    asset.company = None
    assert composition_for_asset(_FakeSession([]), asset) is None


# --------------------------------------------------------------------------- #
# update_company_profile — деривация числа акций из капитализации/цены
# --------------------------------------------------------------------------- #
def test_update_profile_derives_shares():
    company = Company(id=1, name="X")
    asset = Asset(ticker="X", name="X", company_id=1)
    price = Price(asset_id=1, close=200.0)
    facts = [
        FundamentalFact("market_cap", 4.0e11, "RUB", "2024", "x"),
        FundamentalFact("free_float", 30.0, "pct", "2024", "x"),
    ]
    ok = update_company_profile(_FakeSession([price], company=company), asset, facts)
    assert ok is True
    assert company.market_cap == 4.0e11
    assert company.free_float == 30.0
    assert company.shares == 4.0e11 / 200.0   # = 2e9


def test_update_profile_noop_without_company():
    asset = Asset(ticker="X", name="X", company_id=None)
    assert update_company_profile(_FakeSession([]), asset, []) is False
