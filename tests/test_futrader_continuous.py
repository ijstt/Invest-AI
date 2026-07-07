"""Тесты Трек 2 / T2.1: склейка непрерывного фьючерсного контракта (чистое ядро, без БД)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from geoanalytics.futrader.continuous import stitch_continuous
from geoanalytics.futrader.data import INTERVAL_CODES


def _bar(y, m, d, close, h=12):
    ts = datetime(y, m, d, h, 0, tzinfo=UTC)
    return {"ts": ts, "open": close, "high": close, "low": close, "close": close, "volume": 1.0}


def test_interval_codes_map_to_iss():
    # День (24) добавлен для глубины (Фаза 0); минута/10м/час — интрадей-путь T2.1.
    assert INTERVAL_CODES == {"1m": 1, "10m": 10, "1h": 60, "1d": 24}


def test_single_contract_unchanged():
    c = {"secid": "BRN6", "expiry": date(2026, 7, 1),
         "bars": [_bar(2026, 6, 1, 70.0), _bar(2026, 6, 2, 71.0)]}
    series = stitch_continuous([c])
    assert [round(b.close, 2) for b in series.bars] == [70.0, 71.0]
    assert series.rolls == []          # один контракт — роллов нет


def test_two_contracts_ratio_adjust_seam_continuous():
    # c0 (экспирация раньше) торгуется в июне ~100; c1 — в июле ~110. Латест (c1) не трогаем,
    # ранний c0 домножаем на 110/100 = 1.1, чтобы на стыке не было скачка.
    c0 = {"secid": "SiM6", "expiry": date(2026, 6, 15),
          "bars": [_bar(2026, 6, 1, 100.0), _bar(2026, 6, 10, 100.0)]}
    c1 = {"secid": "SiU6", "expiry": date(2026, 9, 15),
          "bars": [_bar(2026, 6, 20, 110.0), _bar(2026, 7, 1, 112.0)]}
    series = stitch_continuous([c0, c1])
    closes = [round(b.close, 2) for b in series.bars]
    # ранний контракт поднят ×1.1 → 110.0, поздний как есть → 110.0, 112.0
    assert closes == [110.0, 110.0, 110.0, 112.0]
    assert len(series.rolls) == 1
    roll = series.rolls[0]
    assert roll["from_secid"] == "SiM6" and roll["to_secid"] == "SiU6"
    assert round(roll["factor"], 4) == 1.1


def test_unsorted_input_is_sorted_by_expiry():
    # Подаём в обратном порядке — склейка сама упорядочит по экспирации.
    c_late = {"secid": "SiU6", "expiry": date(2026, 9, 15), "bars": [_bar(2026, 6, 20, 110.0)]}
    c_early = {"secid": "SiM6", "expiry": date(2026, 6, 15), "bars": [_bar(2026, 6, 1, 100.0)]}
    series = stitch_continuous([c_late, c_early])
    # первым идёт ранний бар (1 июня), последним — поздний (20 июня)
    assert series.bars[0].contract_secid == "SiM6"
    assert series.bars[-1].contract_secid == "SiU6"


def test_empty_input():
    assert stitch_continuous([]).bars == []
    assert stitch_continuous([{"secid": "X", "expiry": None, "bars": []}]).bars == []
