"""Трек 2 / Фаза A→C: тесты разбора микроструктуры стакана (parse_depth, чистое ядро без сети)."""

from __future__ import annotations

from geoanalytics.futrader.depth import parse_depth


def _md(**fields) -> dict:
    """Собрать ISS-подобный payload блока marketdata из именованных полей одной строки."""
    cols = list(fields)
    return {"marketdata": {"columns": cols, "data": [[fields[c] for c in cols]]}}


class TestParseDepth:
    def test_full_microstructure_imbalance(self):
        snap = parse_depth(_md(BID=72.80, OFFER=72.86, SPREAD=0.06,
                               BIDDEPTHT=300, OFFERDEPTHT=100, NUMBIDS=12))
        assert snap["best_bid"] == 72.80
        assert snap["best_ask"] == 72.86
        assert snap["spread"] == 0.06
        assert snap["bid_vol"] == 300 and snap["ask_vol"] == 100
        assert snap["imbalance"] == (300 - 100) / 400      # перевес бидов → +0.5
        assert snap["levels"] == 12
        assert snap["bids"] is None and snap["asks"] is None  # полный L2 анонимно недоступен

    def test_spread_only_still_captured(self):
        # MOEX анонимно для FORTS часто отдаёт только SPREAD — снимок всё равно пишем.
        snap = parse_depth(_md(BID=None, OFFER=None, SPREAD=0.06,
                               BIDDEPTHT=None, OFFERDEPTHT=None))
        assert snap is not None
        assert snap["spread"] == 0.06
        assert snap["best_bid"] is None and snap["imbalance"] is None

    def test_spread_derived_from_bid_ask(self):
        snap = parse_depth(_md(BID=100.0, OFFER=100.5))
        assert snap["spread"] == 0.5                        # выведен из best bid/ask

    def test_empty_block_is_none(self):
        assert parse_depth({"marketdata": {"columns": [], "data": []}}) is None
        assert parse_depth({}) is None

    def test_all_null_is_none(self):
        # Совсем пусто (рынок закрыт) — ни спреда, ни котировок, ни глубины.
        assert parse_depth(_md(BID=None, OFFER=None, SPREAD=None,
                               BIDDEPTHT=None, OFFERDEPTHT=None)) is None

    def test_zero_total_depth_imbalance_none(self):
        snap = parse_depth(_md(SPREAD=0.1, BIDDEPTHT=0, OFFERDEPTHT=0))
        assert snap["imbalance"] is None                    # деление на ноль не валит
