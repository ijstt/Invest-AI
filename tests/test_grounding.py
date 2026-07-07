"""Тесты человекочитаемого рендерера grounding (чистые функции, без БД)."""

from __future__ import annotations

from geoanalytics.context import grounding as g


# --- интерпретаторы ---
def test_rsi_zone():
    assert g.rsi_zone(75) == "перекупленность"
    assert g.rsi_zone(25) == "перепроданность"
    assert g.rsi_zone(50) == "нейтрально"


def test_trend_text_position_to_sma():
    t = {"last": 100, "sma50": 90, "sma200": 80, "trend": "up"}
    txt = g.trend_text(t)
    assert "восходящий" in txt and "выше SMA50" in txt and "выше SMA200" in txt


def test_corr_strength():
    assert g.corr_strength(-0.8) == "сильная обратная"
    assert g.corr_strength(0.3) == "умеренная прямая"
    assert g.corr_strength(0.05) == "слабая прямая"


def test_mood():
    assert g.mood({"negative": 36, "neutral": 12, "positive": 2}) == "преобладает негатив"
    assert g.mood({"negative": 1, "neutral": 2, "positive": 10}) == "преобладает позитив"


def test_rate_stance():
    assert g.rate_stance(16) == "очень жёсткая ДКП"
    assert g.rate_stance(14.5) == "жёсткая ДКП"
    assert g.rate_stance(5) == "мягкая ДКП"


def test_macd_text_cross_and_none():
    assert "бычий" in g.macd_text({"macd": 1.2, "macd_signal": 0.8, "macd_hist": 0.4})
    assert "медвежий" in g.macd_text({"macd": -0.5, "macd_signal": 0.1})
    assert g.macd_text({"macd": 1.0}) is None       # нет сигнальной → None


def test_bollinger_text_position():
    assert "верхней" in g.bollinger_text({"last": 119, "boll_lower": 100, "boll_upper": 120})
    assert "нижней" in g.bollinger_text({"last": 101, "boll_lower": 100, "boll_upper": 120})
    assert "середине" in g.bollinger_text({"last": 110, "boll_lower": 100, "boll_upper": 120})
    assert g.bollinger_text({"last": 110}) is None


def test_section_sentiment_renders_tone_and_divergence():
    out = g._section_sentiment({"ewma": -0.3, "breadth": -0.4, "diverging": True})
    body = " ".join(out)
    assert "негативный" in body and "ДИВЕРГЕНЦИЯ" in body
    assert g._section_sentiment({}) == []
    assert g._section_sentiment({"ewma": None}) == []


def test_render_grounding_includes_macd_bollinger_and_sentiment():
    drivers = {"technical": {"last": 119, "rsi14": 72, "macd": 1.2, "macd_signal": 0.8,
                             "boll_lower": 100, "boll_upper": 120, "trend": "up", "sma50": 110},
               "sentiment_trend": {"ewma": 0.3, "breadth": 0.5, "diverging": False}}
    out = g.render_grounding(drivers)
    assert "MACD" in out and "Боллинджер" in out and "НАСТРОЕНИЕ" in out


# --- рендер целиком ---
def test_render_grounding_sections_and_skips_empty():
    drivers = {
        "technical": {"last": 312.4, "sma50": 300, "sma200": 290, "rsi14": 72,
                      "ret_1m": 6.4, "trend": "up", "vol_annual": 28.0},
        "macro": {"key_rate": 14.5, "fx": {"USD": 92.3},
                  "commodities": {"brent": 78.0}, "external_rates": {"fed_funds": 5.5}},
        "factors": {"sector": "Банки", "macro_factors": ["ключевая ставка ЦБ"],
                    "peers": ["VTBR", "TCSG"]},
        "impacting_events": [{"type": "дивиденды", "direction": "позитив",
                              "magnitude": 0.7, "title": "Набсовет рекомендовал дивиденды"}],
        "correlations": {"usd_rub": -0.45, "sector_peers": 0.78},
        "news": {"recent_count": 14, "sentiment": {"positive": 8, "neutral": 4, "negative": 2},
                 "top_events": [("дивиденды", 3)]},
    }
    out = g.render_grounding(drivers, header="ОБЪЕКТ: Сбербанк (SBER), сектор «Банки».",
                             related=["Сектор Банки: средняя доходность +4%."])
    assert "ОБЪЕКТ: Сбербанк" in out
    assert "ТЕХНИЧЕСКИЙ АНАЛИЗ:" in out and "перекупленность" in out and "выше SMA50" in out
    assert "Ключевая ставка ЦБ 14.5% — жёсткая ДКП" in out
    assert "Brent 78.0" in out and "ставка ФРС США 5.5%" in out
    assert "СЕКТОР И ФАКТОРЫ:" in out and "Банки" in out
    assert "СОБЫТИЯ" in out and "Набсовет" in out
    assert "сильная прямая" in out  # sector_peers 0.78
    assert "преобладает позитив" in out
    assert "СВЯЗАННЫЕ ОБЪЕКТЫ:" in out


def test_render_grounding_empty_drivers():
    # Пустой контекст → пустая строка (не падает, не плодит заголовки секций).
    assert g.render_grounding({}) == ""


def test_render_grounding_partial():
    # Только техника — остальные секции опускаются.
    out = g.render_grounding({"technical": {"last": 100, "sma50": 90, "trend": "up"}})
    assert "ТЕХНИЧЕСКИЙ АНАЛИЗ:" in out
    assert "МАКРО" not in out and "СОБЫТИЯ" not in out
