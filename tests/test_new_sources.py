"""Тесты новых источников M4: RSS-ленты (РБК/Ведомости/Коммерсантъ) и
макро-API ФРС (FRED) / ЕЦБ (ECB). Сеть замокана (_fetch_feed monkeypatch + respx)."""

from __future__ import annotations

import httpx
import respx

from geoanalytics.connectors import available, get_connector
from geoanalytics.connectors.ecb import _latest_from_csv

_RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Сбербанк отчитался о рекордной прибыли</title>
    <description>Чистая прибыль выросла на 10%</description>
    <link>https://example.com/news/1</link>
    <guid>news-1</guid>
    <pubDate>Wed, 03 Jun 2026 10:00:00 +0300</pubDate>
  </item>
</channel></rss>"""


def test_new_sources_registered():
    """Новые источники M4 видны в реестре."""
    assert {"rbc", "vedomosti", "kommersant", "fred", "ecb"} <= set(available())


def test_rss_connector_parses_feed(monkeypatch):
    """RSS-коннектор разбирает ленту в RawItem с нужным payload."""
    from geoanalytics.connectors import rss

    # Мокаем сетевой слой (_fetch_feed), реальный feedparser парсит фикстуру.
    monkeypatch.setattr(rss, "_fetch_feed", lambda _url: _RSS_XML.encode("utf-8"))
    conn = get_connector("rbc")
    items = list(conn.fetch())

    # У РБК одна живая лента (news/30/full.rss) → один item на запись фикстуры.
    assert len(items) == 1
    item = items[0]
    assert item.source == "rbc"
    assert item.payload["title"] == "Сбербанк отчитался о рекордной прибыли"
    assert item.payload["url"] == "https://example.com/news/1"
    assert "прибыли" in item.raw_text


@respx.mock
def test_fred_connector_yields_macro(monkeypatch):
    """FRED-коннектор отдаёт макро-серии с датой в формате dd.mm.yyyy."""
    from geoanalytics.connectors import fred

    monkeypatch.setattr(
        fred, "get_settings", lambda: type("S", (), {"fred_api_key": "test-key"})()
    )

    def _respond(request: httpx.Request) -> httpx.Response:
        series = request.url.params["series_id"]
        # Свежее наблюдение пустое (".") — коннектор должен взять следующее непустое.
        value = "4.33" if series == "FEDFUNDS" else "4.21"
        return httpx.Response(200, json={"observations": [
            {"date": "2026-06-01", "value": "."},
            {"date": "2026-05-01", "value": value},
        ]})

    respx.get(fred.OBSERVATIONS_URL).mock(side_effect=_respond)

    items = {i.payload["indicator"]: i.payload for i in get_connector("fred").fetch()}
    assert set(items) == {"fed_funds", "us_10y"}
    assert items["fed_funds"]["value"] == 4.33
    assert items["fed_funds"]["date"] == "01.05.2026"
    assert items["fed_funds"]["kind"] == "macro"


def test_fred_without_key_skips(monkeypatch):
    """Без API-ключа FRED тихо пропускается (graceful degradation)."""
    from geoanalytics.connectors import fred

    monkeypatch.setattr(
        fred, "get_settings", lambda: type("S", (), {"fred_api_key": None})()
    )
    assert list(get_connector("fred").fetch()) == []


def test_ecb_latest_from_csv_picks_last_observation():
    """Парсер CSV ЕЦБ берёт последнее непустое наблюдение."""
    csv_text = (
        "KEY,FREQ,TIME_PERIOD,OBS_VALUE\n"
        "FM.D.U2.EUR.4F.KR.DFR.LEV,D,2026-05-30,\n"
        "FM.D.U2.EUR.4F.KR.DFR.LEV,D,2026-06-02,2.15\n"
    )
    assert _latest_from_csv(csv_text) == ("2026-06-02", 2.15)


@respx.mock
def test_ecb_connector_yields_macro():
    """ECB-коннектор отдаёт ставки как макро-серии."""
    csv_text = "KEY,TIME_PERIOD,OBS_VALUE\nx,2026-06-02,2.15\n"
    respx.get(url__regex=r"https://data-api\.ecb\.europa\.eu/.*").mock(
        return_value=httpx.Response(200, text=csv_text)
    )

    items = {i.payload["indicator"]: i.payload for i in get_connector("ecb").fetch()}
    assert set(items) == {"ecb_dfr", "ecb_mrr"}
    assert items["ecb_dfr"]["value"] == 2.15
    assert items["ecb_dfr"]["date"] == "02.06.2026"
