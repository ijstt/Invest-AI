"""Тесты отбора заголовков для сводки рынка (ранжирование по значимости)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from geoanalytics.query.news_summary import MarketSnapshot, _news, _rank_score, _stance


class _FakeSession:
    """Имитирует session для `_news`: scalars → статьи, execute → связи (пусто)."""

    def __init__(self, articles):
        self._articles = articles

    def scalars(self, _stmt):
        return self._articles

    def execute(self, _stmt):
        return []  # без тикер-связей


def _article(idx, sig, title, when):
    return SimpleNamespace(
        id=idx, title=title, sentiment="neutral", event_type="macro",
        url=None, published_at=when, significance=sig,
        source="interfax", source_ref=None, factuality="fact",
    )


def test_news_by_significance_surfaces_important_over_fresh():
    """Сводка рынка: наверх идёт значимое, а не свежий низкозначимый шум."""
    now = datetime.now(UTC)
    arts = [
        _article(1, 0.15, "свежий шум (график работы)", now),   # самый свежий, но мусор
        _article(2, 0.85, "ЦБ снизил ключевую ставку", now - timedelta(hours=2)),
        _article(3, 0.15, "Roblox выполнил требования", now - timedelta(hours=1)),
    ]
    snap = MarketSnapshot()
    head = _news(_FakeSession(arts), snap, hours=24, headline_n=2, by_significance=True)
    assert head[0].id == 2                       # значимая новость первой
    assert [h["significance"] for h in snap.headlines][0] == 0.85
    assert "Roblox" not in snap.headlines[0]["title"]


def test_news_market_event_type_breaks_significance_tie():
    """При равной значимости макро/регуляторика идут выше чисто гуманитарной геополитики."""
    now = datetime.now(UTC)
    geo = SimpleNamespace(id=1, title="ВСУ атаковали — пострадали люди", sentiment="negative",
                          event_type="geopolitics", url=None, published_at=now, significance=0.85,
                          source="interfax", source_ref=None, factuality="fact")
    macro = SimpleNamespace(id=2, title="ЦБ изменил ключевую ставку", sentiment="neutral",
                            event_type="macro", url=None,
                            published_at=now - timedelta(hours=3), significance=0.85,
                            source="interfax", source_ref=None, factuality="fact")
    snap = MarketSnapshot()
    head = _news(_FakeSession([geo, macro]), snap, hours=24, headline_n=2, by_significance=True)
    # макро (приоритет 4) обгоняет геополитику (2) при равном sig 0.85, несмотря на меньшую свежесть
    assert head[0].id == 2
    # но геополитика НЕ исчезает — остаётся в выдаче ниже
    assert head[1].id == 1


def test_rank_score_geo_high_sig_beats_macro_low_sig():
    """Геополитика sig=0.85 обгоняет макро sig=0.5 (непрерывный скор, не ступени)."""
    now = datetime.now(UTC)
    geo_score = _rank_score(0.85, "geopolitics", now)    # 0.85 * 0.64 * ~1.0 ≈ 0.54
    macro_score = _rank_score(0.5, "macro", now)         # 0.50 * 0.88 * ~1.0 ≈ 0.44
    assert geo_score > macro_score, (
        f"геополитика sig=0.85 ({geo_score:.3f}) должна обгонять макро sig=0.5 ({macro_score:.3f})"
    )


def test_rank_score_freshness_decay():
    """Старая новость теряет вес — 2-суточная должна быть заметно слабее свежей."""
    now = datetime.now(UTC)
    fresh = _rank_score(0.85, "macro", now)
    stale = _rank_score(0.85, "macro", now - timedelta(days=2))
    assert fresh > stale * 1.5, "свежая новость должна иметь >1.5× вес по сравнению с 2-суточной"


def test_market_stance_from_ewma_and_breadth():
    """B1-консенсус: стойка рынка по тональному моментуму и ширине."""
    assert _stance(0.3, 0.4) == "позитивная"
    assert _stance(-0.3, -0.4) == "негативная"
    assert _stance(0.0, 0.5) == "нейтральная"
    assert _stance(0.3, -0.4) == "смешанная"      # моментум вверх, ширина вниз


def test_news_recency_keeps_chronological_for_feed():
    """Живая лента (by_significance=False) — хронологический порядок, как раньше."""
    now = datetime.now(UTC)
    arts = [
        _article(1, 0.15, "самый свежий", now),
        _article(2, 0.85, "значимый, но старше", now - timedelta(hours=5)),
    ]
    snap = MarketSnapshot()
    head = _news(_FakeSession(arts), snap, hours=24, headline_n=2, by_significance=False)
    assert head[0].id == 1                        # порядок как пришёл (по свежести)
