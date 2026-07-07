"""Тесты F7 source reliability: точность по исходам, усадка, множитель достоверности."""

from __future__ import annotations

from geoanalytics.analytics.source_reliability import (
    DEFAULT_PRIOR,
    TRUST_HIGH,
    credibility_multiplier,
    directional_accuracy,
    reliability_score,
    trust_prior,
)


class TestTrustPrior:
    def test_known_and_unknown(self):
        assert trust_prior("centralbank_russia") == TRUST_HIGH
        assert trust_prior("неизвестный_канал") == DEFAULT_PRIOR
        assert trust_prior(None) == DEFAULT_PRIOR


class TestDirectionalAccuracy:
    def test_sign_match(self):
        pairs = [
            ("positive", 2.0),    # hit
            ("negative", -1.5),   # hit
            ("positive", -3.0),   # miss
            ("negative", 1.0),    # miss
        ]
        assert directional_accuracy(pairs) == (2, 4)

    def test_neutral_and_noise_excluded(self):
        pairs = [
            ("neutral", 5.0),     # нейтраль — не учитывается
            ("positive", 0.1),    # |abn| < порога шума — не учитывается
            ("positive", 3.0),    # hit
        ]
        assert directional_accuracy(pairs) == (1, 1)

    def test_none_abn_excluded(self):
        assert directional_accuracy([("positive", None)]) == (0, 0)


class TestReliabilityScore:
    def test_no_data_returns_prior(self):
        assert reliability_score(0.7, 0, 0) == 0.7

    def test_large_n_approaches_empirical(self):
        # 900/1000 при сильной выборке → ближе к эмпирике, чем к априору 0.3.
        score = reliability_score(0.3, 900, 1000, strength=20)
        assert score > 0.85

    def test_shrinkage_between(self):
        # Малая выборка тянет к априору.
        score = reliability_score(0.7, 0, 4, strength=20)
        assert 0.55 < score < 0.7


class TestCredibilityMultiplier:
    def test_fact_high_reliability(self):
        assert credibility_multiplier(1.0, "fact") == 1.0

    def test_rumor_and_opinion_penalized(self):
        base = credibility_multiplier(0.8, "fact")
        assert credibility_multiplier(0.8, "rumor") < base
        assert credibility_multiplier(0.8, "opinion") < credibility_multiplier(0.8, "rumor")

    def test_floor_and_ceiling(self):
        assert credibility_multiplier(0.0, "opinion") >= 0.5
        assert credibility_multiplier(2.0, "fact") <= 1.0
