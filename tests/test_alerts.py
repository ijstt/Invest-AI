"""Тесты алертов (M5.3): чистые правила, дедуп-ключи, Telegram-канал (сеть замокана)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import respx

from config.settings import Settings
from geoanalytics.alerts import channels
from geoanalytics.alerts.engine import _is_muted
from geoanalytics.alerts.rules import (
    Alert,
    combo_alerts,
    negative_spike_alerts,
    new_event_alerts,
    portfolio_alerts,
    price_move_alerts,
    technical_alerts,
)

BUCKET = "2026-06-04"


# --- настройки исключений из алертов (MMF kind / сигнальные каналы source_ref) ---
def test_alert_exclude_sets_parse():
    s = Settings(alert_exclude_kinds="fund, Index ",
                 alert_exclude_sources="id1432156247, stari_trader,")
    assert s.alert_exclude_kind_set == frozenset({"fund", "index"})
    assert s.alert_exclude_source_set == frozenset({"id1432156247", "stari_trader"})


def test_alert_exclude_sets_empty():
    s = Settings(alert_exclude_kinds="", alert_exclude_sources="")
    assert s.alert_exclude_kind_set == frozenset()
    assert s.alert_exclude_source_set == frozenset()


# --- price_move_alerts ---
def test_price_move_triggers_above_threshold():
    moves = [
        {"ticker": "SBER", "change_pct": 6.2, "last": 300.0},   # ≥ 5 → warning
        {"ticker": "GAZP", "change_pct": -2.0, "last": 120.0},  # < 5 → пропуск
    ]
    out = price_move_alerts(moves, threshold_pct=5.0, bucket=BUCKET)
    assert len(out) == 1
    a = out[0]
    assert a.ticker == "SBER" and a.alert_type == "price_move"
    assert a.severity == "warning"
    assert a.dedup_key == "price:SBER:2026-06-04"
    assert a.payload["change_pct"] == 6.2


def test_price_move_critical_on_double_threshold():
    out = price_move_alerts([{"ticker": "VTBR", "change_pct": -11.0, "last": 0.02}],
                            threshold_pct=5.0, bucket=BUCKET)
    assert out[0].severity == "critical"
    assert "▼" in out[0].title


def test_price_move_ignores_missing_fields():
    out = price_move_alerts([{"ticker": None, "change_pct": 9.0},
                             {"ticker": "X", "change_pct": None}],
                            threshold_pct=5.0, bucket=BUCKET)
    assert out == []


# --- negative_spike_alerts ---
def test_negative_spike_triggers_on_count_and_ratio():
    scopes = [
        {"scope": "MARKET", "ticker": None, "negative": 6, "total": 10},  # 60% ≥ 50%, 6 ≥ 3
        {"scope": "SBER", "ticker": "SBER", "negative": 2, "total": 3},   # count 2 < 3 → нет
        {"scope": "GAZP", "ticker": "GAZP", "negative": 4, "total": 10},  # 40% < 50% → нет
    ]
    out = negative_spike_alerts(scopes, min_count=3, min_ratio=0.5, bucket=BUCKET)
    assert len(out) == 1
    assert out[0].ticker is None
    assert out[0].dedup_key == "neg:MARKET:2026-06-04"
    assert out[0].payload == {"negative": 6, "total": 10, "ratio": 0.6,
                              "kind": "market", "object": None}


def test_negative_spike_sector_and_theme_scopes():
    """Сектор/тема: метка-объект в тексте, префиксованный dedup-ключ, kind/object в payload."""
    scopes = [
        {"scope": "sector:3", "ticker": None, "kind": "sector",
         "object": "Нефть и газ", "label": "по сектору «Нефть и газ»",
         "negative": 5, "total": 6},
        {"scope": "theme:1", "ticker": None, "kind": "theme",
         "object": "Санкции", "label": "по теме «Санкции»",
         "negative": 4, "total": 5},
    ]
    out = negative_spike_alerts(scopes, min_count=3, min_ratio=0.5, bucket=BUCKET)
    assert len(out) == 2
    sec, theme = out
    assert sec.ticker is None and sec.dedup_key == "neg:sector:3:2026-06-04"
    assert "по сектору «Нефть и газ»" in sec.title
    assert sec.payload["kind"] == "sector" and sec.payload["object"] == "Нефть и газ"
    assert theme.dedup_key == "neg:theme:1:2026-06-04"
    assert "по теме «Санкции»" in theme.title


def test_negative_spike_per_asset_dedup_key():
    out = negative_spike_alerts(
        [{"scope": "SBER", "ticker": "SBER", "negative": 5, "total": 6}],
        min_count=3, min_ratio=0.5, bucket=BUCKET,
    )
    assert out[0].dedup_key == "neg:SBER:2026-06-04"
    assert out[0].ticker == "SBER"


def test_negative_spike_zero_total_safe():
    out = negative_spike_alerts([{"scope": "X", "ticker": "X", "negative": 0, "total": 0}],
                                min_count=1, min_ratio=0.1, bucket=BUCKET)
    assert out == []


# --- combo_alerts (D3) ---
def _price(ticker, chg):
    return price_move_alerts([{"ticker": ticker, "change_pct": chg, "last": 1.0}],
                             threshold_pct=2.0, bucket=BUCKET)


def _neg(ticker, neg, total):
    return negative_spike_alerts([{"scope": ticker, "ticker": ticker,
                                   "negative": neg, "total": total}],
                                 min_count=1, min_ratio=0.1, bucket=BUCKET)


def test_combo_fires_on_price_drop_and_negative_spike_same_ticker():
    """Падение цены И всплеск негатива по одному активу → один critical-комбо."""
    out = combo_alerts(_price("SBER", -4.0), _neg("SBER", 5, 6), BUCKET)
    assert len(out) == 1
    a = out[0]
    assert a.alert_type == "combo" and a.ticker == "SBER" and a.severity == "critical"
    assert a.dedup_key == "combo:SBER:2026-06-04"
    assert a.payload == {"change_pct": -4.0, "negative": 5, "total": 6}


def test_combo_ignores_price_rise():
    """Рост цены + негатив — не комбо (сигналы не сонаправлены)."""
    assert combo_alerts(_price("SBER", 4.0), _neg("SBER", 5, 6), BUCKET) == []


def test_combo_ignores_when_only_one_signal():
    assert combo_alerts(_price("SBER", -4.0), [], BUCKET) == []       # нет негатива
    assert combo_alerts([], _neg("SBER", 5, 6), BUCKET) == []         # нет движения


def test_combo_ignores_market_scope_negative():
    """Рыночный всплеск негатива (ticker=None) не порождает комбо по активу."""
    market_neg = negative_spike_alerts(
        [{"scope": "MARKET", "ticker": None, "negative": 8, "total": 10}],
        min_count=1, min_ratio=0.1, bucket=BUCKET)
    assert combo_alerts(_price("SBER", -4.0), market_neg, BUCKET) == []


# --- new_event_alerts ---
def test_new_event_dedup_by_event_id_and_severity():
    events = [
        {"event_id": 42, "event_type": "sanctions", "title": "Новые санкции",
         "impacts": [{"ticker": "SBER"}, {"ticker": "VTBR"}]},
        {"event_id": 43, "event_type": "dividends", "title": "Дивиденды",
         "impacts": [{"ticker": "LKOH"}]},
    ]
    out = new_event_alerts(events)
    assert out[0].dedup_key == "event:42"
    assert out[0].severity == "critical"   # sanctions
    assert out[0].ticker == "SBER"         # топ-импакт
    assert out[1].severity == "warning"    # dividends
    assert out[1].ticker == "LKOH"


def test_new_event_skips_without_id():
    assert new_event_alerts([{"event_type": "macro", "title": "x"}]) == []


def test_new_event_drops_noise_type():
    """Нерыночный шум (тип `noise`: спорт/происшествия/культура) не алертится никогда."""
    events = [
        {"event_id": 10, "event_type": "noise", "title": "Вертолёт пропал в Приморье",
         "impacts": [{"ticker": "SNGS"}]},   # даже с импактом — мусор не уведомляем
        {"event_id": 11, "event_type": "sanctions", "title": "Санкции",
         "impacts": [{"ticker": "SBER"}]},
    ]
    out = new_event_alerts(events)
    assert [a.payload["event_id"] for a in out] == [11]


# --- D1: гейт require_impact_types (отсечение неоценимого шума) ---
def test_new_event_gate_drops_required_type_without_impact():
    """geopolitics без impacts отсекается, с impacts — проходит."""
    events = [
        {"event_id": 1, "event_type": "geopolitics", "title": "Без актива", "impacts": []},
        {"event_id": 2, "event_type": "geopolitics", "title": "С активом",
         "impacts": [{"ticker": "GAZP"}]},
    ]
    out = new_event_alerts(events, require_impact_types=frozenset({"geopolitics"}))
    assert [a.payload["event_id"] for a in out] == [2]
    assert out[0].ticker == "GAZP"


def test_new_event_gate_is_type_specific():
    """Гейт точечный: не-geopolitics без impacts проходит."""
    events = [{"event_id": 3, "event_type": "macro", "title": "Макро", "impacts": []}]
    out = new_event_alerts(events, require_impact_types=frozenset({"geopolitics"}))
    assert len(out) == 1 and out[0].ticker is None


def test_new_event_empty_gate_keeps_all():
    """Пустое множество ничего не отсекает (поведение как до D1)."""
    events = [{"event_id": 4, "event_type": "geopolitics", "title": "x", "impacts": []}]
    assert len(new_event_alerts(events)) == 1
    assert len(new_event_alerts(events, require_impact_types=frozenset())) == 1


# --- technical_alerts (D2) ---
def test_technical_rsi_extremes():
    items = [
        {"ticker": "SBER", "bucket": BUCKET, "rsi": 75.0},
        {"ticker": "GAZP", "bucket": BUCKET, "rsi": 22.0},
        {"ticker": "LKOH", "bucket": BUCKET, "rsi": 50.0},   # норма — без алерта
    ]
    out = technical_alerts(items, rsi_low=30.0, rsi_high=70.0)
    kinds = {(a.ticker, a.payload["kind"]) for a in out}
    assert ("SBER", "rsi_overbought") in kinds
    assert ("GAZP", "rsi_oversold") in kinds
    assert all(a.ticker != "LKOH" for a in out)
    assert all(a.alert_type == "technical" for a in out)


def test_technical_52w_and_cross_and_volume():
    items = [{
        "ticker": "MGNT", "bucket": BUCKET, "rsi": 50.0,
        "at_52w_high": True, "cross": "golden", "vol_ratio": 4.0,
    }]
    out = technical_alerts(items, vol_spike_ratio=3.0)
    kinds = {a.payload["kind"] for a in out}
    assert kinds == {"new_52w_high", "golden_cross", "volume_spike"}
    # dedup-ключи детерминированы и различимы по виду условия
    assert {a.dedup_key for a in out} == {
        f"tech:MGNT:{k}:{BUCKET}" for k in ("new_52w_high", "golden_cross", "volume_spike")
    }


def test_technical_volume_below_threshold_and_death_cross():
    items = [{"ticker": "VTBR", "bucket": BUCKET, "vol_ratio": 2.0, "cross": "death"}]
    out = technical_alerts(items, vol_spike_ratio=3.0)
    kinds = {a.payload["kind"] for a in out}
    assert kinds == {"death_cross"}          # объём 2.0 < 3.0 — без всплеска


def test_technical_skips_without_ticker():
    assert technical_alerts([{"bucket": BUCKET, "rsi": 90.0}]) == []


# --- portfolio_alerts (#6) ---
def _pf_report(**kw):
    from geoanalytics.analytics.portfolio import PortfolioReport
    return PortfolioReport(**kw)


def _pos(ticker, pnl_pct):
    from geoanalytics.analytics.portfolio import PositionReport
    return PositionReport(ticker=ticker, quantity=1, pnl_pct=pnl_pct)


def test_portfolio_drawdown_and_holding():
    # max_drawdown_pct — положительная величина просадки (_max_drawdown).
    report = _pf_report(max_drawdown_pct=12.0,
                        positions=[_pos("SBER", -20.0), _pos("GAZP", -3.0)])
    out = portfolio_alerts(report, bucket=BUCKET, drawdown_pct=10.0, holding_pnl_pct=15.0)
    kinds = {a.payload["kind"] for a in out}
    assert kinds == {"drawdown", "holding"}
    dd = next(a for a in out if a.payload["kind"] == "drawdown")
    assert dd.alert_type == "portfolio" and dd.ticker is None
    assert dd.dedup_key == f"portfolio:drawdown:{BUCKET}"
    hold = next(a for a in out if a.payload["kind"] == "holding")
    assert hold.ticker == "SBER"
    assert hold.dedup_key == f"portfolio:holding:SBER:{BUCKET}"


def test_portfolio_alerts_personal_owner_tagged():
    """5c: с user_id алерт адресуется владельцу и dedup получает суффикс :u{id}."""
    report = _pf_report(max_drawdown_pct=12.0, positions=[_pos("SBER", -20.0)])
    out = portfolio_alerts(report, bucket=BUCKET, drawdown_pct=10.0,
                           holding_pnl_pct=15.0, user_id=42)
    dd = next(a for a in out if a.payload["kind"] == "drawdown")
    assert dd.user_id == 42 and dd.dedup_key == f"portfolio:drawdown:u42:{BUCKET}"
    hold = next(a for a in out if a.payload["kind"] == "holding")
    assert hold.user_id == 42
    assert hold.dedup_key == f"portfolio:holding:SBER:u42:{BUCKET}"


def test_delivery_targets_owner_addressed_and_broadcast():
    """5c: адресный алерт идёт только владельцу; broadcast — всем не замьютившим."""
    from datetime import UTC, datetime

    from geoanalytics.alerts.engine import _delivery_targets
    from geoanalytics.alerts.rules import Alert

    now = datetime.now(UTC)
    recipients = [{"user_id": 1, "chat_id": "111", "mutes": []},
                  {"user_id": 2, "chat_id": "222", "mutes": []}]

    personal = Alert(alert_type="portfolio", title="t", message="m",
                     dedup_key="k1", user_id=2)
    assert _delivery_targets(personal, recipients, [], now) == (["222"], False)

    broadcast = Alert(alert_type="neg_spike", title="t", message="m", dedup_key="k2")
    targets, muted = _delivery_targets(broadcast, recipients, [], now)
    assert set(targets) == {"111", "222"} and muted is False

    # нет получателей (пустая users) → фолбэк на allowlist настроек
    assert _delivery_targets(broadcast, [], [], now) == (None, False)

    # адресный владельцу, которого нет среди allowed → подавлен
    orphan = Alert(alert_type="portfolio", title="t", message="m",
                   dedup_key="k3", user_id=999)
    assert _delivery_targets(orphan, recipients, [], now) == ([], True)


def test_portfolio_below_thresholds_silent():
    report = _pf_report(max_drawdown_pct=5.0, positions=[_pos("SBER", -8.0)])
    assert portfolio_alerts(report, bucket=BUCKET, drawdown_pct=10.0,
                            holding_pnl_pct=15.0) == []


def test_portfolio_empty_report_silent():
    report = _pf_report(error="портфель пуст")
    assert portfolio_alerts(report, bucket=BUCKET, drawdown_pct=10.0,
                            holding_pnl_pct=15.0) == []


def test_portfolio_holding_without_pnl_skipped():
    # P&L не посчитан (нет avg_price) → позиция не алертится.
    report = _pf_report(max_drawdown_pct=2.0, positions=[_pos("SBER", None)])
    assert portfolio_alerts(report, bucket=BUCKET, drawdown_pct=10.0,
                            holding_pnl_pct=15.0) == []


def test_require_impact_type_set_parsing():
    """Property настройки парсит список через запятую (lower/strip, пустые отброшены)."""
    assert Settings(alert_require_impact_types="Geopolitics, Sanctions ,").\
        require_impact_type_set == frozenset({"geopolitics", "sanctions"})
    assert Settings(alert_require_impact_types="").require_impact_type_set == frozenset()


# --- channels (Telegram замокан) ---
def _alert() -> Alert:
    return Alert(alert_type="price_move", ticker="SBER", severity="critical",
                 title="SBER: ▲ +6.00%", message="тест", dedup_key="price:SBER:x")


# --- _is_muted (чистая функция подавления) ---
NOW = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)


def test_mute_by_ticker():
    mutes = [{"scope_type": "ticker", "scope_value": "SBER", "until": None}]
    assert _is_muted(_alert(), mutes, NOW) is True
    other = Alert(alert_type="price_move", ticker="GAZP", severity="info",
                  title="t", message="m", dedup_key="k")
    assert _is_muted(other, mutes, NOW) is False


def test_mute_by_type():
    mutes = [{"scope_type": "type", "scope_value": "price_move", "until": None}]
    assert _is_muted(_alert(), mutes, NOW) is True
    ev = Alert(alert_type="new_event", ticker="SBER", severity="info",
               title="t", message="m", dedup_key="k")
    assert _is_muted(ev, mutes, NOW) is False


def test_mute_by_ticker_type_pair():
    mutes = [{"scope_type": "ticker_type", "scope_value": "SBER:price_move", "until": None}]
    assert _is_muted(_alert(), mutes, NOW) is True
    mutes2 = [{"scope_type": "ticker_type", "scope_value": "SBER:new_event", "until": None}]
    assert _is_muted(_alert(), mutes2, NOW) is False


def test_mute_expired_is_ignored():
    expired = [{"scope_type": "ticker", "scope_value": "SBER",
                "until": NOW - timedelta(hours=1)}]
    assert _is_muted(_alert(), expired, NOW) is False
    active = [{"scope_type": "ticker", "scope_value": "SBER",
               "until": NOW + timedelta(hours=1)}]
    assert _is_muted(_alert(), active, NOW) is True


def test_mute_market_alert_not_matched_by_ticker_scope():
    """Рыночный алерт (ticker=None) не подавляется правилом по тикеру."""
    market = Alert(alert_type="neg_spike", ticker=None, severity="warning",
                   title="рынок", message="m", dedup_key="k")
    mutes = [{"scope_type": "ticker", "scope_value": "SBER", "until": None}]
    assert _is_muted(market, mutes, NOW) is False


def test_format_text_has_icon_and_title():
    text = channels.format_text(_alert())
    assert "📈" in text and "SBER" in text


def test_dispatch_console_only_when_telegram_unconfigured():
    s = Settings(telegram_bot_token=None, telegram_chat_id=None)
    assert channels.dispatch(_alert(), s) == ["console"]


@respx.mock
def test_dispatch_to_telegram_on_success():
    route = respx.post(url__regex=r"https://api\.telegram\.org/bot.*/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    s = Settings(telegram_bot_token="TOKEN", telegram_chat_id="123")
    delivered = channels.dispatch(_alert(), s)
    assert delivered == ["console", "telegram"]
    assert route.called


@respx.mock
def test_dispatch_telegram_failure_is_graceful():
    respx.post(url__regex=r"https://api\.telegram\.org/.*").mock(
        return_value=httpx.Response(500)
    )
    s = Settings(telegram_bot_token="TOKEN", telegram_chat_id="123")
    # Сбой доставки не валит прогон — остаётся console.
    assert channels.dispatch(_alert(), s) == ["console"]


def test_telegram_chat_ids_parsing():
    assert Settings(telegram_chat_id=None).telegram_chat_ids == []
    assert Settings(telegram_chat_id="123").telegram_chat_ids == ["123"]
    assert Settings(telegram_chat_id=" 111 , 222 ,").telegram_chat_ids == ["111", "222"]


@respx.mock
def test_dispatch_to_multiple_recipients(monkeypatch):
    # Пауза между отправками не должна замедлять тест.
    monkeypatch.setattr(channels, "_SEND_GAP_SEC", 0)
    route = respx.post(url__regex=r"https://api\.telegram\.org/bot.*/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    s = Settings(telegram_bot_token="TOKEN", telegram_chat_id="111,222")
    delivered = channels.dispatch(_alert(), s)
    assert delivered == ["console", "telegram"]
    assert route.call_count == 2  # ушло обоим получателям


@respx.mock
def test_dispatch_multi_partial_success_still_delivered(monkeypatch):
    # Один получатель недоступен (chat not found), другой — ок: канал доставлен.
    monkeypatch.setattr(channels, "_SEND_GAP_SEC", 0)
    calls = {"n": 0}

    def responder(request):
        calls["n"] += 1
        return httpx.Response(200, json={"ok": True}) if calls["n"] == 1 else httpx.Response(400)

    respx.post(url__regex=r"https://api\.telegram\.org/.*").mock(side_effect=responder)
    s = Settings(telegram_bot_token="TOKEN", telegram_chat_id="111,222")
    assert channels.dispatch(_alert(), s) == ["console", "telegram"]


def test_price_move_zscore_normalizes_by_volatility():
    """G1: одинаковые 3% — алерт для спокойного актива (σ=0.8), тишина для волатильного (σ=3)."""
    from geoanalytics.alerts.rules import price_move_alerts

    moves = [
        {"ticker": "CALM", "change_pct": 3.0, "last": 100.0, "sigma_pct": 0.8},
        {"ticker": "WILD", "change_pct": 3.0, "last": 100.0, "sigma_pct": 3.0},
    ]
    out = price_move_alerts(moves, 5.0, "2026-06-11", zscore_threshold=3.0, min_abs_pct=1.5)
    assert [a.ticker for a in out] == ["CALM"]
    assert out[0].payload["z"] == 3.75
    assert out[0].payload["sigma_pct"] == 0.8


def test_price_move_zscore_floor_blocks_micro_moves():
    """G1: высокий z, но микродвижение ниже floor — не алертим."""
    from geoanalytics.alerts.rules import price_move_alerts

    moves = [{"ticker": "TINY", "change_pct": 0.9, "last": 10.0, "sigma_pct": 0.2}]
    out = price_move_alerts(moves, 5.0, "b", zscore_threshold=3.0, min_abs_pct=1.5)
    assert out == []


def test_price_move_zscore_falls_back_to_fixed_without_sigma():
    """G1: нет σ (короткая история) — работает старый фикс. порог."""
    from geoanalytics.alerts.rules import price_move_alerts

    moves = [
        {"ticker": "NOSIG", "change_pct": 6.0, "last": 100.0, "sigma_pct": None},
        {"ticker": "NOSIG2", "change_pct": 4.0, "last": 100.0},
    ]
    out = price_move_alerts(moves, 5.0, "b", zscore_threshold=3.0, min_abs_pct=1.5)
    assert [a.ticker for a in out] == ["NOSIG"]


def test_price_move_zscore_critical_on_double_z():
    from geoanalytics.alerts.rules import price_move_alerts

    moves = [{"ticker": "X", "change_pct": 7.0, "last": 1.0, "sigma_pct": 1.0}]
    out = price_move_alerts(moves, 5.0, "b", zscore_threshold=3.0, min_abs_pct=1.5)
    assert out[0].severity == "critical"  # z=7 ≥ 2×3


# --- J3: _enrich_price_alerts ---
def test_enrich_price_alerts_adds_attribution_to_message():
    """J3: атрибуция добавляет строку с факторами в сообщение алерта."""
    from datetime import date
    from unittest.mock import patch

    from geoanalytics.alerts.engine import _enrich_price_alerts
    from geoanalytics.analytics.attribution import AttributionResult

    alert = Alert(
        alert_type="price_move", ticker="SBER", severity="warning",
        title="SBER: ▼ -3.00%", message="SBER изменился на -3.00%.",
        dedup_key="price:SBER:2026-06-12",
        payload={"change_pct": -3.0},
    )
    mock_result = AttributionResult(
        ticker="SBER", day=date(2026, 6, 12),
        asset_return_pct=-3.0, alpha_pct=0.05,
        contributions_pct={"market": -2.0, "sector": -0.5},
        idio_pct=-0.55, r2=0.72, n_obs=200,
    )
    with patch("geoanalytics.alerts.engine.attribute_asset", return_value=mock_result):
        _enrich_price_alerts(object(), [alert])  # session не нужен при моке

    assert "Факторы" in alert.message
    assert "market" in alert.message
    assert "идиосинкразия" in alert.message
    assert alert.payload["attribution"]["r2"] == 0.72


def test_enrich_price_alerts_graceful_on_error():
    """J3: если атрибуция падает, алерт остаётся неизменным."""
    from unittest.mock import patch

    from geoanalytics.alerts.engine import _enrich_price_alerts

    alert = Alert(
        alert_type="price_move", ticker="SBER", severity="warning",
        title="SBER", message="original",
        dedup_key="k", payload={},
    )
    with patch("geoanalytics.alerts.engine.attribute_asset", side_effect=RuntimeError("ой")):
        _enrich_price_alerts(object(), [alert])

    assert alert.message == "original"
    assert "attribution" not in alert.payload


def test_enrich_price_alerts_skips_non_price_move():
    """J3: не-price_move алерты не обрабатываются."""
    from unittest.mock import patch

    from geoanalytics.alerts.engine import _enrich_price_alerts

    alert = Alert(
        alert_type="neg_spike", ticker="SBER", severity="info",
        title="neg", message="msg", dedup_key="k",
    )
    with patch("geoanalytics.alerts.engine.attribute_asset") as mock_attr:
        _enrich_price_alerts(object(), [alert])
    mock_attr.assert_not_called()
