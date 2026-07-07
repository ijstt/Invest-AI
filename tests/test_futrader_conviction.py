"""Трек 2 / Объективный вход (A1/A5): тесты консенсуса и агрегатора conviction (чистые ядра)."""

from __future__ import annotations

from geoanalytics.analytics.recommendation import Driver
from geoanalytics.futrader import conviction as C
from geoanalytics.futrader.conviction import (
    consensus_driver,
    entry_conviction,
    gather_entry_drivers,
)


class TestGatherTimeframePurity:
    """Чистота ТФ (Фаза A): на интрадей-входе старшие-ТФ драйверы A2/A3 не участвуют."""

    def _patch(self, monkeypatch, called):
        monkeypatch.setattr(C, "consensus_driver",
                            lambda *a, **k: Driver("consensus", "C", 0.5, 1.0))
        monkeypatch.setattr(C, "daily_trend_driver",
                            lambda *a, **k: called.append("daily")
                            or Driver("daily_trend", "D", 0.5, 0.8))
        monkeypatch.setattr("geoanalytics.futrader.underlying.underlying_drivers",
                            lambda *a, **k: (called.append("underlying"), [])[1])
        monkeypatch.setattr(C, "scenario_driver", lambda *a, **k: None)

    def test_intraday_excludes_daily(self, monkeypatch):
        called: list[str] = []
        self._patch(monkeypatch, called)
        drivers = gather_entry_drivers(None, ticker="BR", asset_code="BR",
                                       signals_by_strat={}, idx=0, intraday=True)
        assert called == []                              # A2/A3 не вызывались
        assert [d.key for d in drivers] == ["consensus"]

    def test_daily_includes_higher_tf(self, monkeypatch):
        called: list[str] = []
        self._patch(monkeypatch, called)
        gather_entry_drivers(None, ticker="BR", asset_code="BR",
                             signals_by_strat={}, idx=0, intraday=False)
        assert "daily" in called and "underlying" in called


class TestConsensus:
    def test_all_agree_long(self):
        sbs = {"a": [1, 1], "b": [0, 1], "c": [0, 1]}
        d = consensus_driver(sbs, 1)
        assert d is not None and d.contribution > 0 and d.sign == 1

    def test_all_agree_short(self):
        sbs = {"a": [-1], "b": [-1], "c": [0]}
        d = consensus_driver(sbs, 0)
        assert d is not None and d.contribution < 0 and d.sign == -1

    def test_split_near_zero(self):
        sbs = {"a": [1], "b": [-1], "c": [1], "d": [-1]}
        d = consensus_driver(sbs, 0)
        assert d is not None and abs(d.contribution) < 0.3

    def test_no_votes_none(self):
        sbs = {"a": [0], "b": [0]}
        assert consensus_driver(sbs, 0) is None


class TestEntryConviction:
    def test_agreement_passes(self):
        drivers = [Driver("consensus", "Консенсус", 0.8, 1.0),
                   Driver("daily_trend", "Дневной", 0.6, 0.8)]
        ec = entry_conviction(1, drivers)
        assert ec.passes and ec.score > 0 and ec.conviction > 0
        assert ec.risk_multiplier >= 1.0

    def test_disagreement_blocks(self):
        # доказательства бычьи, а правило предлагает шорт → блок (disagree)
        drivers = [Driver("consensus", "Консенсус", 0.8, 1.0),
                   Driver("daily_trend", "Дневной", 0.7, 0.8)]
        ec = entry_conviction(-1, drivers)
        assert not ec.passes and ec.reason == "disagree"

    def test_weak_blocks(self):
        # знак согласен, но совокупность слабая → блок (weak)
        drivers = [Driver("consensus", "Консенсус", 0.05, 1.0)]
        ec = entry_conviction(1, drivers, min_conviction=0.5)
        assert not ec.passes and ec.reason == "weak"

    def test_empty_fail_open(self):
        # нет доказательств → не блокируем (петля созревания), множитель риска нейтрален
        ec = entry_conviction(1, [])
        assert ec.passes and ec.risk_multiplier == 1.0 and ec.drivers == []

    def test_risk_multiplier_scales_with_conviction(self):
        strong = entry_conviction(1, [Driver("consensus", "К", 0.95, 1.0),
                                       Driver("daily_trend", "Д", 0.9, 1.0),
                                       Driver("underlying_trend", "Б", 0.9, 1.0)])
        weak = entry_conviction(1, [Driver("consensus", "К", 0.3, 1.0)])
        assert strong.passes and weak.passes
        assert strong.risk_multiplier > weak.risk_multiplier

    def test_breakdown_shape(self):
        ec = entry_conviction(1, [Driver("consensus", "Консенсус", 0.8, 1.0, "3↑")])
        bd = ec.as_breakdown()
        assert bd and set(bd[0]) == {"key", "label", "sign", "contribution", "detail"}
