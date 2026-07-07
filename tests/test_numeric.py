"""F5 numeric extraction: правила извлечения дивидендов/ставки/сумм сделок."""

from __future__ import annotations

import pytest

from geoanalytics.nlp.numeric import (
    DEAL_AMOUNT,
    DIVIDEND,
    KEY_RATE,
    TARGET_PRICE,
    extract_numbers,
)


def _only(text: str):
    facts = extract_numbers(text)
    assert len(facts) == 1, facts
    return facts[0]


# --------------------------------------------------------------------------- #
# Дивиденды.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("Совет директоров рекомендовал дивиденды в 235 рублей на акцию", 235.0),
        ("дивиденды за I квартал 2026 года в размере 5,19 руб. на акцию", 5.19),
        ("рекордные дивиденды в размере ₽37,64 на акцию", 37.64),
        ("дивиденды из расчёта 17,39 рубля на обыкновенную акцию", 17.39),
    ],
)
def test_dividend(text: str, value: float) -> None:
    fact = _only(text)
    assert (fact.kind, fact.unit) == (DIVIDEND, "RUB")
    assert fact.value == pytest.approx(value)


def test_dividend_requires_currency() -> None:
    # Год из «за 2026 год» не должен превращаться в дивиденд.
    assert extract_numbers("обсудили дивиденды за 2026 год на собрании") == []


def test_dividend_yield_is_not_dividend() -> None:
    assert extract_numbers("объявил дивиденды с доходностью более 14%") == []


# --------------------------------------------------------------------------- #
# Ключевая ставка.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("ЦБ снизил ключевую ставку 19 июня до 14%", 14.0),
        ("ЦБ сохранил ключевую ставку на уровне 16% годовых", 16.0),
        ("ключевая ставка составляет 21%", 21.0),
    ],
)
def test_key_rate(text: str, value: float) -> None:
    fact = _only(text)
    assert (fact.kind, fact.unit) == (KEY_RATE, "pct")
    assert fact.value == pytest.approx(value)


def test_key_rate_range_skipped() -> None:
    # Диапазон без решения — не целевое значение ставки.
    assert extract_numbers("назвал ключевую ставку 10-12% психологической чертой") == []


@pytest.mark.parametrize(
    "text",
    [
        # Прогноз/ожидание — не значение ставки.
        "Шохин допустил снижение ключевой ставки 19 июня до 14%",
        "аналитики ожидают снижения ключевой ставки до 12%",
    ],
)
def test_key_rate_forecast_skipped(text: str) -> None:
    assert extract_numbers(text) == []


def test_plain_pct_without_rate_context() -> None:
    assert extract_numbers("инфляция замедлилась до 6,2%") == []


def test_foreign_plural_rates_skipped() -> None:
    # «три ключевые ставки» ЕЦБ — не ключевая ставка ЦБ РФ.
    assert extract_numbers(
        "ЕЦБ повысил три ключевые ставки на 25 базисных пунктов — до 2,25%"
    ) == []


# --------------------------------------------------------------------------- #
# Сумма сделки.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "value", "unit"),
    [
        ("«Яндекс» продал «Авто.ру» за 35 млрд рублей", 35e9, "RUB"),
        ("Индия закупит истребители за $38,9 млрд", 38.9e9, "USD"),
        ("банк хотят купить за около €30 млрд", 30e9, "EUR"),
        ("Росимущество продает активы за 7,8 млрд рублей", 7.8e9, "RUB"),
    ],
)
def test_deal_amount(text: str, value: float, unit: str) -> None:
    fact = _only(text)
    assert (fact.kind, fact.unit) == (DEAL_AMOUNT, unit)
    assert fact.value == pytest.approx(value)


@pytest.mark.parametrize(
    "text",
    [
        # Выручка/объёмы — не сделка (нет глагола сделки перед суммой).
        "Мировые продажи чипов выросли вдвое — до $110,5 млрд",
        "Нефтегазовые допдоходы в мае составили 175 млрд рублей",
        # Штуки, а не деньги (валюты нет).
        "пресечена продажа 630 млн единиц продукции",
        "ВВП вырос на 3 млрд рублей",
        # Агрегатные потоки покупок — не сделка (только предлог «за»).
        "Минфин в июне купит валюту и золото на 208,2 млрд рублей",
        "«физики» остались нетто-покупателями акций на 19,9 млрд рублей",
        "выкуп акций на 10 млрд руб. одобрен",
    ],
)
def test_deal_amount_noise_skipped(text: str) -> None:
    assert extract_numbers(text) == []


def test_thousands_separator_and_dedup() -> None:
    facts = extract_numbers(
        "Компания продала актив за 1 200,5 млн рублей. "
        "Повтор: продала актив за 1 200,5 млн рублей."
    )
    assert len(facts) == 1
    assert facts[0].value == pytest.approx(1_200_500_000.0)


def test_multiple_kinds_in_one_text() -> None:
    facts = extract_numbers(
        "ЦБ снизил ключевую ставку до 14%. Совет директоров рекомендовал "
        "дивиденды в размере 25 руб. на акцию."
    )
    kinds = {f.kind for f in facts}
    assert kinds == {KEY_RATE, DIVIDEND}


def test_snippet_present() -> None:
    fact = _only("«Яндекс» продал «Авто.ру» за 35 млрд рублей")
    assert "35 млрд" in fact.snippet


# --------------------------------------------------------------------------- #
# Целевая цена (F10).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "value", "unit"),
    [
        ("Целевая цена Сбербанка — 350 руб.", 350.0, "RUB"),
        ("Повышаем таргет по бумаге до 342,5 руб", 342.5, "RUB"),
        ("Справедливая стоимость акции $42", 42.0, "USD"),
        ("целевая цена ₽1 250 на горизонте года", 1250.0, "RUB"),
    ],
)
def test_target_price(text: str, value: float, unit: str) -> None:
    fact = _only(text)
    assert (fact.kind, fact.unit) == (TARGET_PRICE, unit)
    assert fact.value == pytest.approx(value)


def test_target_price_requires_currency() -> None:
    # «таргет 350» без валюты — слишком сомнительно (precision-first).
    assert extract_numbers("ставим таргет 350 по акции") == []


def test_target_price_percent_is_not_price() -> None:
    # Потенциал роста в процентах — не целевая цена.
    assert extract_numbers("целевая цена даёт потенциал роста 20%") == []
