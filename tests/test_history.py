"""Тесты бэкфилла истории: парсеры XML ЦБ — курсы (XML_dynamic) и металлы (xml_metall)."""

from __future__ import annotations

from datetime import UTC, datetime

from geoanalytics.analytics.history import (
    CANDLES_URL,
    FUTURES_CANDLES_URL,
    INDEX_CANDLES_URL,
    _fetch_candles,
    parse_fred_observations,
    parse_fx_dynamic,
    parse_metal_dynamic,
)

_XML = """<?xml version="1.0" encoding="windows-1251"?>
<ValCurs ID="R01235" DateRange1="01.06.2021" DateRange2="03.06.2021"
         name="Foreign Currency Market Dynamic">
  <Record Date="01.06.2021" Id="R01235">
    <Nominal>1</Nominal><Value>73,2965</Value><VunitRate>73,2965</VunitRate>
  </Record>
  <Record Date="02.06.2021" Id="R01235">
    <Nominal>1</Nominal><Value>73,2411</Value><VunitRate>73,2411</VunitRate>
  </Record>
</ValCurs>""".encode("cp1251")

# CNY: номинал 10 — Value за 10 юаней, VunitRate за один.
_XML_NOMINAL = """<?xml version="1.0" encoding="windows-1251"?>
<ValCurs ID="R01375">
  <Record Date="05.03.2022" Id="R01375">
    <Nominal>10</Nominal><Value>180,5000</Value><VunitRate>18,05</VunitRate>
  </Record>
</ValCurs>""".encode("cp1251")

_XML_BROKEN = """<?xml version="1.0" encoding="windows-1251"?>
<ValCurs>
  <Record Date="не дата" Id="R01235"><VunitRate>73,1</VunitRate></Record>
  <Record Date="01.06.2021" Id="R01235"><Nominal>1</Nominal></Record>
  <Record Id="R01235"><VunitRate>73,1</VunitRate></Record>
  <Record Date="02.06.2021" Id="R01235"><VunitRate>не число</VunitRate></Record>
</ValCurs>""".encode("cp1251")


class TestParseFxDynamic:
    def test_parses_records(self):
        points = parse_fx_dynamic(_XML)
        assert points == [
            (datetime(2021, 6, 1, tzinfo=UTC), 73.2965),
            (datetime(2021, 6, 2, tzinfo=UTC), 73.2411),
        ]

    def test_uses_vunit_rate_not_value(self):
        # У валют с номиналом 10/100 берём курс ЗА ЕДИНИЦУ (VunitRate).
        points = parse_fx_dynamic(_XML_NOMINAL)
        assert points == [(datetime(2022, 3, 5, tzinfo=UTC), 18.05)]

    def test_skips_broken_records(self):
        assert parse_fx_dynamic(_XML_BROKEN) == []


# xml_metall: коды 1..4 → gold/silver/platinum/palladium, цена ₽/г в <Sell>.
_XML_METALS = """<?xml version="1.0" encoding="windows-1251"?>
<Metall FromDate="20260601" ToDate="20260603" name="Precious metals quotations">
  <Record Date="02.06.2026" Code="1"><Buy>10457,9</Buy><Sell>10457,9</Sell></Record>
  <Record Date="02.06.2026" Code="2"><Buy>174,34</Buy><Sell>174,34</Sell></Record>
  <Record Date="02.06.2026" Code="3"><Buy>4428,44</Buy><Sell>4428,44</Sell></Record>
  <Record Date="02.06.2026" Code="4"><Buy>3183,87</Buy><Sell>3183,87</Sell></Record>
  <Record Date="03.06.2026" Code="1"><Buy>10379,54</Buy><Sell>10379,54</Sell></Record>
</Metall>""".encode("cp1251")

_XML_METALS_BROKEN = """<?xml version="1.0" encoding="windows-1251"?>
<Metall>
  <Record Date="02.06.2026" Code="9"><Sell>1,0</Sell></Record>
  <Record Date="не дата" Code="1"><Sell>1,0</Sell></Record>
  <Record Code="1"><Sell>1,0</Sell></Record>
  <Record Date="02.06.2026" Code="1"><Buy>1,0</Buy></Record>
  <Record Date="02.06.2026" Code="2"><Sell>не число</Sell></Record>
</Metall>""".encode("cp1251")


class TestParseMetalDynamic:
    def test_parses_all_four_metals(self):
        points = parse_metal_dynamic(_XML_METALS)
        d2 = datetime(2026, 6, 2, tzinfo=UTC)
        assert ("gold", d2, 10457.9) in points
        assert ("silver", d2, 174.34) in points
        assert ("platinum", d2, 4428.44) in points
        assert ("palladium", d2, 3183.87) in points
        assert ("gold", datetime(2026, 6, 3, tzinfo=UTC), 10379.54) in points
        assert len(points) == 5

    def test_skips_unknown_codes_and_broken(self):
        assert parse_metal_dynamic(_XML_METALS_BROKEN) == []


class TestParseFredObservations:
    def test_parses_and_skips_empty(self):
        payload = {"observations": [
            {"date": "2026-06-01", "value": "66.25"},
            {"date": "2026-06-02", "value": "."},          # выходной/пусто — пропуск
            {"date": "2026-06-03", "value": "67.10"},
        ]}
        points = parse_fred_observations(payload)
        assert points == [
            (datetime(2026, 6, 1, tzinfo=UTC), 66.25),
            (datetime(2026, 6, 3, tzinfo=UTC), 67.10),
        ]

    def test_empty_and_broken(self):
        assert parse_fred_observations({}) == []
        assert parse_fred_observations(
            {"observations": [{"date": "bad", "value": "1"}, {"value": "2"}]}
        ) == []


class TestCandleMarketRouting:
    """C2: выбор рынка ISS для свечей по kind актива (shares / index / forts)."""

    def _capture_url(self, monkeypatch):
        seen = {}

        def fake_fetch_window(url, frm, till, interval=24):
            seen["url"] = url
            seen["interval"] = interval
            return []                      # пустое окно → один проход, без сети

        import geoanalytics.analytics.history as h
        monkeypatch.setattr(h, "_fetch_window", fake_fetch_window)
        return seen

    def test_share_uses_stock_market(self, monkeypatch):
        seen = self._capture_url(monkeypatch)
        _fetch_candles("SBER", days=1, kind="share")
        assert seen["url"] == CANDLES_URL.format(secid="SBER")

    def test_index_uses_index_market(self, monkeypatch):
        seen = self._capture_url(monkeypatch)
        _fetch_candles("IMOEX", days=1, kind="index")
        assert seen["url"] == INDEX_CANDLES_URL.format(secid="IMOEX")

    def test_future_uses_forts_market(self, monkeypatch):
        seen = self._capture_url(monkeypatch)
        _fetch_candles("BRN6", days=1, kind="future")
        assert seen["url"] == FUTURES_CANDLES_URL.format(secid="BRN6")
        assert "futures/markets/forts" in seen["url"]


class TestFrontFuturesResolve:
    """C2: резолв фронтального контракта FORTS по базовому asset_code."""

    def _rows(self):
        from datetime import UTC, datetime, timedelta
        today = datetime.now(UTC).date()
        d = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
        return [
            {"SECID": "BRN6", "ASSETCODE": "BR", "LASTTRADEDATE": d(15)},   # ближайший живой
            {"SECID": "BRQ6", "ASSETCODE": "BR", "LASTTRADEDATE": d(45)},
            {"SECID": "BRM6", "ASSETCODE": "BR", "LASTTRADEDATE": d(-15)},  # уже истёк
            {"SECID": "SiM6", "ASSETCODE": "Si", "LASTTRADEDATE": d(2)},
        ]

    def _patch(self, monkeypatch):
        import geoanalytics.analytics.history as h
        monkeypatch.setattr(h, "_forts_securities", self._rows)

    def test_picks_nearest_non_expired(self, monkeypatch):
        from geoanalytics.analytics.history import _front_futures_secid
        self._patch(monkeypatch)
        # BRM6 истёк (дата в прошлом) → ближайший живой контракт BRN6.
        assert _front_futures_secid("BR") == "BRN6"

    def test_case_sensitive_assetcode(self, monkeypatch):
        from geoanalytics.analytics.history import _front_futures_secid
        self._patch(monkeypatch)
        assert _front_futures_secid("Si") == "SiM6"      # строчная i сохраняется

    def test_unknown_assetcode_returns_none(self, monkeypatch):
        from geoanalytics.analytics.history import _front_futures_secid
        self._patch(monkeypatch)
        assert _front_futures_secid("ZZZ") is None
