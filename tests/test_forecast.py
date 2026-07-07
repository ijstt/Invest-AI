"""F10 роутер новость↔прогноз для брокерских каналов."""

from __future__ import annotations

import pytest

from geoanalytics.nlp.forecast import BROKER_CHANNELS, is_forecast_post

BROKER = "SberInvestments"


@pytest.mark.parametrize(
    "title",
    [
        "Повышаем целевую цену Сбербанка до 350 руб",
        "Таргет по Лукойлу — 8000 руб на горизонте 12 месяцев",
        "Справедливая стоимость акции выше рынка",
        "Рекомендуем покупать акции Газпрома",
        "Сохраняем рекомендацию: держать",
        "Позитивный взгляд на акции металлургов",
    ],
)
def test_strong_markers_any_channel(title: str) -> None:
    # Сильные маркеры аналитика срабатывают даже без брокерского канала.
    assert is_forecast_post(title, "", channel=None) is True


def test_broker_soft_marker_with_number() -> None:
    # Мягкое ожидание + число на брокерском канале → прогноз.
    assert is_forecast_post(
        "Ожидаем рост бумаги на 15% после отчёта", "", channel=BROKER
    ) is True


def test_broker_soft_marker_with_temporal_forecast() -> None:
    assert is_forecast_post(
        "Ждём сильную отчётность эмитента", "", channel=BROKER,
        temporal_status="forecast",
    ) is True


@pytest.mark.parametrize(
    ("title", "channel"),
    [
        # Обычная новость брокерского канала — не прогноз.
        ("ЦБ снизил ключевую ставку до 14%", BROKER),
        # Мягкий маркер без числа и без F3-прогноза — не прогноз.
        ("Глава ЦБ ожидает замедления инфляции", BROKER),
        # Мягкий маркер вне брокерского канала — не прогноз.
        ("Аналитики ожидают роста на 10%", "ifax_go"),
        # Пустой заголовок.
        ("", BROKER),
    ],
)
def test_news_not_forecast(title: str, channel: str) -> None:
    assert is_forecast_post(title, "", channel=channel) is False


def test_broker_channels_match_settings() -> None:
    # Три брокерских канала из tg.txt.
    assert BROKER_CHANNELS == {
        "SberInvestments", "tb_invest_official", "bcs_world_of_investments",
    }
