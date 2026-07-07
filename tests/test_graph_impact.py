"""Тесты G7: распространение влияния по графу (чистое ядро)."""

from __future__ import annotations

from geoanalytics.analytics.graph_impact import (
    _relation_label,
    propagate_impact,
)


class TestPropagateImpact:
    def test_supply_chain_attenuated(self):
        # 0.5 * 0.4 * 0.6 = 0.12
        assert propagate_impact("positive", 0.5, "supplier_of", 0.4) == ("positive", 0.12)

    def test_competitor_weaker(self):
        # 0.5 * 0.3 * 0.35 = 0.0525 (выше порога)
        d, m = propagate_impact("negative", 0.5, "competitor_of", 0.3)
        assert d == "negative"
        assert m == 0.052   # round(0.0525, 3) → 0.052 (float)

    def test_below_floor_dropped(self):
        # 0.1 * 0.3 * 0.35 = 0.0105 < 0.05 → None
        assert propagate_impact("positive", 0.1, "competitor_of", 0.3) is None

    def test_unknown_predicate(self):
        assert propagate_impact("positive", 1.0, "belongs_to", 1.0) is None

    def test_direction_preserved(self):
        for direction in ("positive", "negative", "neutral"):
            res = propagate_impact(direction, 1.0, "supplier_of", 1.0)
            assert res is not None and res[0] == direction


class TestRelationLabel:
    def test_competitor(self):
        assert _relation_label("competitor_of", True) == "конкурент"
        assert _relation_label("competitor_of", False) == "конкурент"

    def test_supplier_direction(self):
        # supplier_of: subject поставляет объекту.
        assert _relation_label("supplier_of", asset_is_subject=False) == "поставщик"
        assert _relation_label("supplier_of", asset_is_subject=True) == "потребитель"

    def test_subsidiary_direction(self):
        # subsidiary_of: subject (дочка) → object (материнская).
        assert _relation_label("subsidiary_of", asset_is_subject=True) == "материнская компания"
        assert _relation_label("subsidiary_of", asset_is_subject=False) == "дочерняя компания"


class TestSubsidiaryPropagation:
    def test_holding_attenuated(self):
        # L2: 0.4 * 0.5 * 0.55 = 0.11 (выше порога значимости)
        assert propagate_impact("positive", 0.4, "subsidiary_of", 0.5) == ("positive", 0.11)

    def test_direction_preserved(self):
        d, m = propagate_impact("negative", 0.6, "subsidiary_of", 0.6)
        assert d == "negative" and m > 0
