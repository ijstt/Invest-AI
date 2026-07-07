"""Тесты J2: ежедневный дайджест."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from geoanalytics.query.digest import _portfolio_digest_line, build_digest_text


def test_portfolio_digest_line_compact():
    """5c: компактная строка портфеля для персонального дайджеста."""
    rep = SimpleNamespace(error=None, total_value_rub=30620.0, var95_1d_pct=1.6,
                          max_drawdown_pct=14.59,
                          positions=[SimpleNamespace(ticker="NVTK", pnl_pct=-15.1),
                                     SimpleNamespace(ticker="SBER", pnl_pct=4.8)])
    line = _portfolio_digest_line(rep)
    assert "30 620" in line and "VaR95 1.6%" in line and "в минусе: NVTK" in line
    assert "SBER" not in line.split("в минусе:")[1]   # прибыльная не в списке минуса


def test_portfolio_digest_line_empty():
    assert _portfolio_digest_line(SimpleNamespace(error="пуст")) == "💼 Портфель: пуст"


def _fake_snap(key_rate=14.5, fx=None, gainers=None, losers=None, headlines=None):
    return SimpleNamespace(
        key_rate=key_rate,
        key_rate_date="12.06.2026",
        fx=fx or {"USD": 71.91, "EUR": 82.97},
        top_gainers=gainers or [{"ticker": "SBER", "change_pct": 1.5, "last": 300.0}],
        top_losers=losers or [{"ticker": "LKOH", "change_pct": -2.1, "last": 7100.0}],
        headlines=headlines or [
            {"title": "ЦБ снизил ставку", "significance": 0.85, "sentiment": "neutral"},
        ],
    )


def _fake_asset(ticker: str, asset_id: int):
    return SimpleNamespace(ticker=ticker, id=asset_id, kind="share")


def test_digest_contains_macro():
    """Дайджест включает ключевую ставку и курсы."""
    snap = _fake_snap()
    with (
        patch("geoanalytics.query.digest.build_snapshot", return_value=snap),
        patch("geoanalytics.query.digest.session_scope") as mock_ss,
        patch("geoanalytics.query.digest.news_pressure", return_value=0.5),
        patch("geoanalytics.query.digest.latest_momentum", return_value=0.1),
        patch("geoanalytics.query.digest.upcoming_events", return_value=[]),
    ):
        session = MagicMock()
        session.scalars.return_value = [_fake_asset("SBER", 1)]
        mock_ss.return_value.__enter__ = lambda s: session
        mock_ss.return_value.__exit__ = MagicMock(return_value=False)
        text = build_digest_text()

    assert "14.5%" in text
    assert "USD" in text
    assert "71.91" in text


def test_digest_contains_movers():
    """Дайджест включает топ-движения."""
    snap = _fake_snap()
    with (
        patch("geoanalytics.query.digest.build_snapshot", return_value=snap),
        patch("geoanalytics.query.digest.session_scope") as mock_ss,
        patch("geoanalytics.query.digest.news_pressure", return_value=0.0),
        patch("geoanalytics.query.digest.latest_momentum", return_value=None),
        patch("geoanalytics.query.digest.upcoming_events", return_value=[]),
    ):
        session = MagicMock()
        session.scalars.return_value = []
        mock_ss.return_value.__enter__ = lambda s: session
        mock_ss.return_value.__exit__ = MagicMock(return_value=False)
        text = build_digest_text()

    assert "SBER" in text
    assert "+1.5%" in text
    assert "LKOH" in text


def test_digest_contains_headlines():
    """Дайджест включает топ-новости."""
    snap = _fake_snap()
    with (
        patch("geoanalytics.query.digest.build_snapshot", return_value=snap),
        patch("geoanalytics.query.digest.session_scope") as mock_ss,
        patch("geoanalytics.query.digest.news_pressure", return_value=0.0),
        patch("geoanalytics.query.digest.latest_momentum", return_value=None),
        patch("geoanalytics.query.digest.upcoming_events", return_value=[]),
    ):
        session = MagicMock()
        session.scalars.return_value = []
        mock_ss.return_value.__enter__ = lambda s: session
        mock_ss.return_value.__exit__ = MagicMock(return_value=False)
        text = build_digest_text()

    assert "ЦБ снизил ставку" in text
    assert "0.85" in text


def test_digest_contains_calendar_events():
    """Дайджест включает ближайшие события из календаря."""
    snap = _fake_snap(headlines=[])
    events = [
        {"kind": "key_rate", "ticker": None, "title": "Заседание ЦБ",
         "event_date": date(2026, 6, 19), "days_left": 7, "payload": {}},
    ]
    with (
        patch("geoanalytics.query.digest.build_snapshot", return_value=snap),
        patch("geoanalytics.query.digest.session_scope") as mock_ss,
        patch("geoanalytics.query.digest.news_pressure", return_value=0.0),
        patch("geoanalytics.query.digest.latest_momentum", return_value=None),
        patch("geoanalytics.query.digest.upcoming_events", return_value=events),
    ):
        session = MagicMock()
        session.scalars.return_value = []
        mock_ss.return_value.__enter__ = lambda s: session
        mock_ss.return_value.__exit__ = MagicMock(return_value=False)
        text = build_digest_text()

    assert "Заседание ЦБ" in text


def test_digest_asset_sentiment_trend_direction():
    """Тональный моментум отображается со стрелкой направления."""
    snap = _fake_snap(headlines=[], gainers=[], losers=[])
    with (
        patch("geoanalytics.query.digest.build_snapshot", return_value=snap),
        patch("geoanalytics.query.digest.session_scope") as mock_ss,
        patch("geoanalytics.query.digest.news_pressure", return_value=1.0),
        patch("geoanalytics.query.digest.latest_momentum", return_value=-0.3),
        patch("geoanalytics.query.digest.upcoming_events", return_value=[]),
    ):
        session = MagicMock()
        session.scalars.return_value = [_fake_asset("SBER", 1)]
        mock_ss.return_value.__enter__ = lambda s: session
        mock_ss.return_value.__exit__ = MagicMock(return_value=False)
        text = build_digest_text()

    assert "SBER" in text
    assert "▼" in text   # негативный моментум
