"""Тесты F4 rumor/fact: правила фактологичности (fact/rumor/opinion)."""

from __future__ import annotations

from geoanalytics.nlp.rumor import FACT, OPINION, RUMOR, classify_factuality


class TestClassifyFactuality:
    def test_plain_news_is_fact_low_conf(self):
        label, conf = classify_factuality("Сбербанк отчитался о прибыли за квартал.")
        assert label == FACT
        assert conf <= 0.5

    def test_empty_is_fact(self):
        assert classify_factuality("")[0] == FACT

    def test_rumor_markers(self):
        for text in (
            "По данным источников, банк готовит сделку.",
            "Якобы готовится новый пакет санкций.",
            "Инсайдеры сообщают о возможной отставке.",
            "Неофициально стало известно о слиянии.",
        ):
            label, conf = classify_factuality(text)
            assert label == RUMOR, text
            assert conf >= 0.55

    def test_intent_verbs_are_not_rumor(self):
        # Корпоративные планы — нормальная фактура, не слух (precision-first).
        assert classify_factuality("Газпром планирует построить завод.")[0] == FACT
        assert classify_factuality("Совет директоров обсуждает дивиденды.")[0] == FACT

    def test_more_markers_higher_confidence(self):
        one = classify_factuality("По слухам, сделка готовится.")[1]
        many = classify_factuality(
            "По слухам и по данным источников, инсайдеры сообщают о сделке."
        )[1]
        assert many > one

    def test_forecast_alone_is_not_rumor(self):
        # Прогноз — отдельная ось (F3), не слух: аналитика из надёжного канала ценна.
        label, _ = classify_factuality(
            "Рубль укрепится до конца года.", temporal_status="forecast"
        )
        assert label == FACT

    def test_forecast_boosts_confidence_over_marker(self):
        base = classify_factuality("По данным источников, ставку снизят.")[1]
        boosted = classify_factuality(
            "По данным источников, ставку снизят.", temporal_status="forecast"
        )[1]
        assert boosted > base

    def test_opinion_beats_fact(self):
        label, _ = classify_factuality("На мой взгляд, рынок переоценён.")
        assert label == OPINION

    def test_speech_emoji_is_opinion(self):
        label, _ = classify_factuality("Трамп 🗣 о пошлинах против Китая")
        assert label == OPINION

    def test_case_insensitive(self):
        assert classify_factuality("ПО ДАННЫМ ИСТОЧНИКОВ, всё иначе.")[0] == RUMOR
