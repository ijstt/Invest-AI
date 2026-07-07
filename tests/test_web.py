"""Тесты веб-дашборда (M5.2): чистый SVG-хелпер и HTML-роуты на TestClient."""

from __future__ import annotations

from fastapi.testclient import TestClient

from geoanalytics.analytics.backtest import BacktestResult
from geoanalytics.api import app as api_app
from geoanalytics.api import web
from geoanalytics.api.charts import sparkline
from geoanalytics.query.asset_report import AssetReport
from geoanalytics.query.news_summary import MarketSnapshot

client = TestClient(api_app.app)


# --- charts.sparkline (чистая функция) ---
def test_sparkline_basic():
    sp = sparkline([1, 2, 3, 4], width=100, height=40, pad=0)
    assert sp["n"] == 4
    assert sp["up"] is True
    assert sp["min"] == 1 and sp["max"] == 4 and sp["last"] == 4
    pts = sp["points"].split(" ")
    assert len(pts) == 4
    assert pts[0] == "0.0,40.0"        # первая точка: минимум → низ (инверсия Y)
    assert pts[-1] == "100.0,0.0"      # последняя: максимум → верх


def test_sparkline_down_flag():
    assert sparkline([5, 4, 3])["up"] is False


def test_sparkline_insufficient():
    assert sparkline([]) is None
    assert sparkline([1]) is None


# --- HTML-роуты ---
def test_dashboard(monkeypatch):
    snap = MarketSnapshot(key_rate=14.5, key_rate_date="03.06.2026", fx={"USD": 78.0},
                          sentiment_breakdown={"neutral": 2})
    monkeypatch.setattr(web, "build_snapshot", lambda **kw: snap)
    r = client.get("/")
    assert r.status_code == 200
    assert "Сводка рынка" in r.text
    assert "14.5" in r.text


def test_asset_page(monkeypatch):
    report = AssetReport(ticker="SBER", found=True, name="Сбербанк",
                         indicators={"last": 300.0, "rsi14": 55.0})
    from datetime import datetime
    rows = [(datetime(2026, 6, d), 290.0, 296.0, 289.0, 295.0, 1000.0 + d) for d in range(1, 6)]
    monkeypatch.setattr(web, "build_report", lambda *a, **kw: report)
    monkeypatch.setattr(web, "_asset_ohlcv", lambda *a, **kw: rows)
    monkeypatch.setattr(web, "list_assets", lambda: [])
    r = client.get("/ui/asset?ticker=SBER")
    assert r.status_code == 200
    assert "Сбербанк" in r.text
    assert "<polyline" in r.text       # график цены отрисован


def test_asset_page_shows_graph_panel(monkeypatch):
    """G7-панель «Косвенно через граф связей» рендерит report.graph_impacts."""
    from datetime import datetime

    report = AssetReport(
        ticker="LKOH", found=True, name="Лукойл", indicators={"last": 6000.0},
        graph_impacts=[{"via": "ROSN", "relation": "конкурент",
                        "title": "Санкции на нефть", "direction": "negative",
                        "magnitude": 0.12}])
    rows = [(datetime(2026, 6, d), 290.0, 296.0, 289.0, 295.0, 1000.0) for d in range(1, 6)]
    monkeypatch.setattr(web, "build_report", lambda *a, **kw: report)
    monkeypatch.setattr(web, "_asset_ohlcv", lambda *a, **kw: rows)
    monkeypatch.setattr(web, "_sentiment_cells", lambda *a, **kw: [])
    monkeypatch.setattr(web, "list_assets", lambda: [])
    r = client.get("/ui/asset?ticker=LKOH")
    assert r.status_code == 200
    assert "Косвенно через граф связей" in r.text
    assert "конкурент" in r.text and "ROSN" in r.text


def test_portfolio_page_empty(monkeypatch):
    from geoanalytics.analytics.portfolio import PortfolioReport

    monkeypatch.setattr(web, "_portfolio_context", lambda: {
        "report": PortfolioReport(error="портфель пуст — geo portfolio add ТИКЕР КОЛ-ВО"),
        "correlations": [], "exposure": []})
    r = client.get("/ui/portfolio")
    assert r.status_code == 200
    assert "портфель пуст" in r.text
    assert "geo portfolio add" in r.text


def test_portfolio_page_with_positions(monkeypatch):
    from geoanalytics.analytics.portfolio import PortfolioReport, PositionReport

    report = PortfolioReport(
        total_value_rub=150000.0, regime="спокойный",
        positions=[PositionReport(ticker="SBER", quantity=100, last_close=300.0,
                                  value_rub=30000.0, weight_pct=20.0, pnl_pct=5.0,
                                  betas={"market": 1.1}, pressure=0.2, momentum=0.01)],
        daily_vol_pct=1.5, var95_1d_pct=2.4, var95_1d_rub=3600.0,
        exposure={"market": 0.9}, avg_r2=0.42, correlations={("SBER", "GAZP"): 0.6})
    monkeypatch.setattr(web, "_portfolio_context", lambda: {
        "report": report,
        "correlations": [{"pair": "SBER / GAZP", "r": 0.6}],
        "exposure": [("market", 0.9)]})
    r = client.get("/ui/portfolio")
    assert r.status_code == 200
    assert "Позиции" in r.text and "SBER" in r.text
    assert "Факторная экспозиция" in r.text
    assert "Корреляции холдингов" in r.text


def test_portfolio_page_quality_panels(monkeypatch):
    """Качественный просмотр: стоимость во времени, аллокация-кольцо, вклад в риск."""
    from datetime import date

    from geoanalytics.analytics.portfolio import PortfolioReport, PositionReport
    from geoanalytics.api.charts import pie, sparkline

    report = PortfolioReport(
        total_value_rub=50000.0,
        positions=[
            PositionReport(ticker="SBER", quantity=100, last_close=300.0, value_rub=30000.0,
                           weight_pct=60.0, sector="Банки", risk_contribution_pct=70.0),
            PositionReport(ticker="LKOH", quantity=3, last_close=6666.0, value_rub=20000.0,
                           weight_pct=40.0, sector="Нефтегаз", risk_contribution_pct=30.0),
        ],
        value_series=[(date(2026, 1, 1), 48000.0), (date(2026, 6, 1), 50000.0)],
        sector_alloc=[("Банки", 60.0), ("Нефтегаз", 40.0)])
    monkeypatch.setattr(web, "_portfolio_context", lambda: {
        "report": report, "correlations": [], "exposure": [],
        "value_chart": sparkline([v for _, v in report.value_series], width=820, height=200),
        "alloc_pie": pie(report.sector_alloc),
        "risk_rows": report.positions, "risk_max": 70.0})
    r = client.get("/ui/portfolio")
    assert r.status_code == 200
    assert "Стоимость во времени" in r.text
    assert "Аллокация по секторам" in r.text and "Нефтегаз" in r.text
    assert "Вклад в риск" in r.text


def test_portfolio_add_form(monkeypatch):
    from geoanalytics.analytics.portfolio import PortfolioReport

    calls = []
    monkeypatch.setattr(web, "_add_position", lambda *a: calls.append(a))
    monkeypatch.setattr(web, "_portfolio_context", lambda: {
        "report": PortfolioReport(error="портфель пуст"), "correlations": [],
        "exposure": [], "assets": []})
    r = client.post("/ui/portfolio/add",
                    data={"ticker": "SBER", "quantity": "10", "price": "250"})
    assert r.status_code == 200
    assert calls == [("SBER", 10.0, 250.0)]


def test_portfolio_add_form_swallows_bad_input(monkeypatch):
    from geoanalytics.analytics.portfolio import PortfolioReport

    def boom(*a):
        raise ValueError("количество должно быть положительным")

    monkeypatch.setattr(web, "_add_position", boom)
    monkeypatch.setattr(web, "_portfolio_context", lambda: {
        "report": PortfolioReport(error="портфель пуст"), "correlations": [],
        "exposure": [], "assets": []})
    r = client.post("/ui/portfolio/add", data={"ticker": "SBER", "quantity": "-5"})
    assert r.status_code == 200  # страница не падает


def test_portfolio_remove_form(monkeypatch):
    from geoanalytics.analytics.portfolio import PortfolioReport

    calls = []
    monkeypatch.setattr(web, "_remove_position", lambda t: calls.append(t))
    monkeypatch.setattr(web, "_portfolio_context", lambda: {
        "report": PortfolioReport(error="портфель пуст"), "correlations": [],
        "exposure": [], "assets": []})
    r = client.post("/ui/portfolio/remove", data={"ticker": "SBER"})
    assert r.status_code == 200
    assert calls == ["SBER"]


def test_portfolio_cash_row_delete_targets_cash_endpoint(monkeypatch):
    """Крестик в строке кэша шлёт в /ui/portfolio/cash с amount=0 (а не в /remove,
    который кэш-баланс не трогает) — иначе рубли не удаляются."""
    from geoanalytics.analytics.portfolio import PortfolioReport, PositionReport

    report = PortfolioReport(
        total_value_rub=100000.0,
        positions=[PositionReport(ticker="RUB", quantity=100000, last_close=1.0,
                                  value_rub=100000.0, weight_pct=100.0, sector="Кэш",
                                  note="рубли — база портфеля, вне риска")])
    monkeypatch.setattr(web, "_portfolio_context", lambda: {
        "report": report, "correlations": [], "exposure": []})
    r = client.get("/ui/portfolio")
    assert r.status_code == 200
    assert 'hx-post="/ui/portfolio/cash"' in r.text
    assert 'name="currency" value="RUB"' in r.text
    assert 'name="amount" value="0"' in r.text


def test_portfolio_cash_form_zero_amount_removes_balance(monkeypatch):
    """amount=0 через /ui/portfolio/cash удаляет баланс (set_balance(...,0) → delete)."""
    from geoanalytics.analytics.portfolio import PortfolioReport

    calls = []

    class FakeRepo:
        def __init__(self, session):
            pass

        def set_balance(self, currency, amount):
            calls.append((currency, amount))

    monkeypatch.setattr(
        "geoanalytics.storage.repositories.CashBalanceRepository", FakeRepo)
    monkeypatch.setattr(web, "_portfolio_context", lambda: {
        "report": PortfolioReport(error="портфель пуст"), "correlations": [],
        "exposure": []})
    r = client.post("/ui/portfolio/cash", data={"currency": "RUB", "amount": "0"})
    assert r.status_code == 200
    assert calls == [("RUB", 0.0)]


def test_unhandled_exception_returns_html_500(monkeypatch):
    """Аудит #1: исключение в раннере → вежливая 500-страница, не стек."""
    def boom(**kw):
        raise RuntimeError("runner exploded")

    monkeypatch.setattr(web, "build_snapshot", boom)
    safe = TestClient(api_app.app, raise_server_exceptions=False)
    r = safe.get("/", headers={"accept": "text/html"})
    assert r.status_code == 500
    assert "Что-то пошло не так" in r.text


def test_cached_ttl(monkeypatch):
    """Аудит #2: TTL-кэш отдаёт сохранённое в окне и пересчитывает по истечении."""
    calls = []

    def fn():
        calls.append(1)
        return len(calls)

    web._invalidate_cache("t")
    assert web._cached("t", fn) == 1
    assert web._cached("t", fn) == 1        # в пределах TTL → из кэша, fn не зван повторно
    assert len(calls) == 1
    assert web._cached("t", fn, ttl=0) == 2  # ttl=0 форсит пересчёт
    web._invalidate_cache("t")


def test_factors_page(monkeypatch):
    """Страница факторов рендерит карточки сырья/валют с последним уровнем и динамикой."""
    from geoanalytics.analytics import factors as factors_mod
    fs = factors_mod.FactorSeries("brent", "Brent", "$/барр.", "commodity", [], [60.0, 66.0])
    monkeypatch.setattr(factors_mod, "factor_series", lambda s, **k: [fs])
    web._cache.clear()
    r = client.get("/ui/factors")
    assert r.status_code == 200
    assert "Brent" in r.text and "Факторы рынка" in r.text
    web._cache.clear()


def test_asset_partial_empty_ticker():
    r = client.get("/ui/partials/asset?ticker=")
    assert r.status_code == 200
    assert "Введите тикер" in r.text


def test_indicators_partial_period_toggle(monkeypatch):
    """A7: партиал индикаторов рендерит таблицу и тумблер Д/Н/М с активным периодом."""
    monkeypatch.setattr(web, "_indicators_context", lambda t, p="D": {
        "ticker": t.upper(), "indicators": {"last": 300.0, "rsi14": 55.0}, "ind_period": p})
    r = client.get("/ui/partials/asset/indicators?ticker=SBER&period=W")
    assert r.status_code == 200
    assert "RSI(14)" in r.text and "indicators-panel" in r.text
    # тумблер ведёт на все три таймфрейма
    assert "period=D" in r.text and "period=W" in r.text and "period=M" in r.text


def test_indicators_partial_empty_ticker():
    r = client.get("/ui/partials/asset/indicators?ticker=")
    assert "Введите тикер" in r.text


def test_backtest_page(monkeypatch):
    result = BacktestResult(bars=10, total_return_pct=5.0, buy_hold_return_pct=3.0,
                            max_drawdown_pct=1.0, num_trades=2, exposure=0.5, sharpe=0.7,
                            equity_curve=[1.0, 1.02, 1.05])
    monkeypatch.setattr(web, "backtest_asset_cached", lambda *a, **kw: result)
    r = client.get("/ui/backtest?ticker=SBER&strategy=rsi")
    assert r.status_code == 200
    assert "+5.00%" in r.text
    assert "Шарп" in r.text
    assert "<polyline" in r.text       # кривая капитала


def test_backtest_partial_error(monkeypatch):
    def _raise(*a, **kw):
        raise ValueError("Неизвестная стратегия: foo")
    monkeypatch.setattr(web, "backtest_asset_cached", _raise)
    r = client.get("/ui/partials/backtest?ticker=SBER&strategy=foo")
    assert r.status_code == 200
    assert "стратегия" in r.text.lower()


def test_backtest_form_lists_strategies(monkeypatch):
    """Форма бэктеста предлагает все стратегии, включая sentiment."""
    monkeypatch.setattr(web, "list_assets", lambda: [])
    r = client.get("/ui/backtest")
    assert r.status_code == 200
    for s in ("sma_cross", "momentum", "rsi", "sentiment"):
        assert s in r.text


# --- Трек 2: панель песочницы бумажного счёта (/ui/track2) ---
def _track2_ctx_populated():
    from datetime import datetime

    from geoanalytics.api.charts import sparkline
    from geoanalytics.futrader.monitoring import DriftReport
    from geoanalytics.futrader.portfolio_risk import PortfolioRiskReport
    from geoanalytics.futrader.risk_limits import RiskLimits
    from geoanalytics.futrader.track import TrackMetrics, TrackRecord

    metrics = TrackMetrics(n_points=40, total_return_pct=2.5, max_drawdown_pct=3.1, sharpe=0.8,
                           n_trades=12, win_rate=0.58, profit_factor=1.4, avg_win=900.0,
                           avg_loss=-600.0)
    risk = PortfolioRiskReport(n_instruments=2, gross_exposure=120000.0, net_exposure=30000.0,
                               var_pct=2.0, es_pct=3.0, contributions={"BR": 60.0, "RTS": 40.0},
                               top_correlations=[["BR/RTS", 0.42]])
    rec = TrackRecord(account="demo", starting_cash=100000.0, equity=102500.0,
                      realized_pnl=1800.0, unrealized_pnl=700.0, drawdown_pct=1.2,
                      gross_margin=48000.0, open_positions=2, metrics=metrics,
                      by_strategy={"rsi": 1200.0, "macd": -300.0},
                      by_instrument={"BR": 1500.0, "RTS": -600.0}, risk=risk)
    by_strategy, strat_max = web._attr_rows(rec.by_strategy)
    by_instrument, instr_max = web._attr_rows(rec.by_instrument)
    return {
        "account": "demo", "rec": rec, "metrics": metrics, "risk": risk,
        "limits": RiskLimits(), "halt": None,
        "value_chart": sparkline([100000.0, 101000.0, 99500.0, 102500.0]),
        "positions": [{"asset_code": "BR", "interval": "1h", "source": "rsi", "net_qty": 1,
                       "avg_price": 70.5, "last_price": 71.2, "realized_pnl": 0.0}],
        "trades": [{"ts": datetime(2026, 6, 20, 14, 0), "asset_code": "BR", "source": "rsi",
                    "action": "buy", "signed_qty": 1, "price": 70.5, "p_win": 0.61,
                    "realized_pnl": None, "reason": "entry", "conviction": 0.42}],
        "drift": [DriftReport(source="rsi", psi_max=0.18, psi_worst_feature="rsi14",
                              live_calib_gap=0.03, win_rate_live=0.55, win_rate_expected=0.6,
                              win_rate_decay=0.05, n_live_trades=22, should_halt=False)],
        "by_strategy": by_strategy, "strat_max": strat_max,
        "by_instrument": by_instrument, "instr_max": instr_max}


def test_track2_page(monkeypatch):
    """Панель Трека 2 рендерит эквити/метрики/риск/позиции/сделки/дрейф."""
    monkeypatch.setattr(web, "_track2_context", _track2_ctx_populated)
    r = client.get("/ui/track2")
    assert r.status_code == 200
    assert "Трейдер" in r.text
    assert "102,500" in r.text                      # эквити
    assert "Портфельный риск" in r.text and "VaR" in r.text
    assert "P&amp;L по стратегиям" in r.text and "rsi" in r.text
    assert "Открытые позиции" in r.text and "BR" in r.text
    assert "Дрейф моделей" in r.text
    assert "<polyline" in r.text                     # кривая эквити


def test_track2_page_empty(monkeypatch):
    """Пустой счёт (нет снимков/позиций) не падает — дружелюбные заглушки."""
    from geoanalytics.futrader.risk_limits import RiskLimits
    from geoanalytics.futrader.track import TrackMetrics, TrackRecord

    rec = TrackRecord(account="demo", starting_cash=100000.0, equity=100000.0,
                      metrics=TrackMetrics(), note="нет снимков эквити — счёт ещё не торговал")
    monkeypatch.setattr(web, "_track2_context", lambda: {
        "account": "demo", "rec": rec, "metrics": rec.metrics, "risk": None,
        "limits": RiskLimits(), "halt": None, "value_chart": None, "positions": [],
        "trades": [], "drift": [], "by_strategy": [], "strat_max": 0.0,
        "by_instrument": [], "instr_max": 0.0})
    r = client.get("/ui/track2")
    assert r.status_code == 200
    assert "Нет открытых позиций" in r.text
    assert "Сделок пока нет" in r.text
    assert "ещё не торговал" in r.text


def test_track2_partial_halted(monkeypatch):
    """Партиал показывает баннер kill-switch при взведённом halt."""
    from datetime import datetime

    from geoanalytics.futrader.risk_limits import RiskLimits
    from geoanalytics.futrader.track import TrackMetrics, TrackRecord

    rec = TrackRecord(account="demo", starting_cash=100000.0, equity=94000.0,
                      metrics=TrackMetrics())
    monkeypatch.setattr(web, "_track2_context", lambda: {
        "account": "demo", "rec": rec, "metrics": rec.metrics, "risk": None,
        "limits": RiskLimits(),
        "halt": {"halted": True, "reason": "дневной убыток 6.2%",
                 "updated_at": datetime(2026, 6, 20, 12, 0)},
        "value_chart": None, "positions": [], "trades": [], "drift": [],
        "by_strategy": [], "strat_max": 0.0, "by_instrument": [], "instr_max": 0.0})
    r = client.get("/ui/partials/track2")
    assert r.status_code == 200
    assert "KILL-SWITCH" in r.text and "дневной убыток" in r.text


# --- M6: лента новостей, автодополнение тикеров, график со свечами ---
def test_news_partial(monkeypatch):
    from datetime import UTC, datetime
    monkeypatch.setattr(web, "recent_headlines", lambda **kw: [
        {"title": "Санкции против банка", "sentiment": "negative", "event_type": "sanctions",
         "url": None, "published_at": datetime(2026, 6, 4, 9, 30, tzinfo=UTC),
         "significance": 0.81, "tickers": ["SBER"]},
    ])
    r = client.get("/ui/partials/news")
    assert r.status_code == 200
    assert "Санкции против банка" in r.text
    assert "SBER" in r.text and "04.06" in r.text


def test_assets_endpoint(monkeypatch):
    monkeypatch.setattr(api_app, "list_assets",
                        lambda: [{"ticker": "SBER", "name": "Сбербанк", "sector": "Банки"}])
    r = client.get("/assets")
    assert r.status_code == 200
    assert r.json() == [{"ticker": "SBER", "name": "Сбербанк", "sector": "Банки"}]


def test_asset_form_has_datalist(monkeypatch):
    monkeypatch.setattr(web, "list_assets",
                        lambda: [{"ticker": "GAZP", "name": "Газпром", "sector": "Нефть и газ"}])
    r = client.get("/ui/asset")
    assert r.status_code == 200
    assert "<datalist" in r.text and "GAZP" in r.text


def test_asset_chart_partial_candles(monkeypatch):
    from datetime import datetime
    rows = [(datetime(2026, 6, d), 10.0, 12.0, 9.0, 11.0, 100.0 + d) for d in range(1, 6)]
    monkeypatch.setattr(web, "_asset_ohlcv", lambda *a, **kw: rows)
    r = client.get("/ui/partials/asset/chart?ticker=SBER&kind=candles")
    assert r.status_code == 200
    assert "<rect" in r.text          # свечи (и/или столбики объёма) отрисованы прямоугольниками
    # а линия — полилинией
    r2 = client.get("/ui/partials/asset/chart?ticker=SBER&kind=line")
    assert "<polyline" in r2.text


def test_chart_indicator_toggles(monkeypatch):
    """Тумблеры: vol=0 убирает сабпанель объёма, osc=0 — RSI."""
    from datetime import datetime, timedelta
    # ≥15 баров — чтобы RSI-панель успела «прогреться».
    rows = [(datetime(2026, 6, 1) + timedelta(days=i), 10.0 + i % 3, 12.0 + i % 3,
             9.0 + i % 3, 11.0 + i % 3, 100.0 + i) for i in range(20)]
    monkeypatch.setattr(web, "_asset_ohlcv", lambda *a, **kw: rows)
    on = client.get("/ui/partials/asset/chart?ticker=SBER&vol=1&osc=1").text
    off = client.get("/ui/partials/asset/chart?ticker=SBER&vol=0&osc=0").text
    # Структурные маркеры: заливка столбиков объёма и подпись RSI-панели (кнопка-тумблер
    # называется просто «RSI», панель — «RSI(14)», поэтому различаем по «RSI(14)»).
    assert 'opacity="0.65"' in on and "RSI(14)" in on
    assert 'opacity="0.65"' not in off and "RSI(14)" not in off


# --- Алерты: лента + управление (ack/mute/unmute) ---
def _alert_dict(**over) -> dict:
    base = {"id": 7, "alert_type": "price_move", "ticker": "SBER", "severity": "critical",
            "title": "SBER: ▲ +6.00%", "message": "тест",
            "created_at": "2026-06-06T12:00:00+00:00", "acknowledged_at": None,
            "channels": [], "payload": {}}
    base.update(over)
    return base


def test_alerts_page(monkeypatch):
    monkeypatch.setattr(web, "recent_alerts", lambda **kw: [_alert_dict()])
    monkeypatch.setattr(web.manage, "list_mutes", lambda: [])
    r = client.get("/ui/alerts")
    assert r.status_code == 200
    assert "SBER: ▲ +6.00%" in r.text
    assert 'href="/ui/asset?ticker=SBER"' in r.text     # тикер кликабелен
    assert "Подавление" in r.text                       # панель mute


def test_alerts_partial_filtered(monkeypatch):
    seen = {}

    def _fake(**kw):
        seen.update(kw)
        return []
    monkeypatch.setattr(web, "recent_alerts", _fake)
    r = client.get("/ui/partials/alerts?severity=critical&alert_type=price_move&only_unacked=true")
    assert r.status_code == 200
    assert seen["severity"] == "critical" and seen["alert_type"] == "price_move"
    assert seen["only_unacked"] is True
    assert "Нет алертов" in r.text


def test_alert_ack_swaps_row(monkeypatch):
    monkeypatch.setattr(web.manage, "acknowledge", lambda *a, **kw: True)
    monkeypatch.setattr(web, "get_alert",
                        lambda _id: _alert_dict(acknowledged_at="2026-06-06T13:00:00+00:00"))
    r = client.post("/ui/alerts/7/ack")
    assert r.status_code == 200
    assert "✓ ack" in r.text
    assert 'id="alert-7"' in r.text


def test_alert_mute_renders_panel(monkeypatch):
    calls = {}

    def _mute(scope_type, scope_value, days, **kw):
        calls.update(scope_type=scope_type, scope_value=scope_value, days=days)
        return 1
    monkeypatch.setattr(web.manage, "mute_for_days", _mute)
    monkeypatch.setattr(web.manage, "list_mutes", lambda: [
        {"id": 1, "scope_type": "ticker", "scope_value": "SBER", "reason": None,
         "until": None, "created_at": "2026-06-06T12:00:00+00:00"}])
    r = client.post("/ui/alerts/mute",
                    data={"scope_type": "ticker", "scope_value": "SBER", "days": "30"})
    assert r.status_code == 200
    assert calls == {"scope_type": "ticker", "scope_value": "SBER", "days": 30}
    assert 'id="mutes-panel"' in r.text and "SBER" in r.text and "бессрочно" in r.text


def test_alert_unmute_renders_panel(monkeypatch):
    removed = {}
    monkeypatch.setattr(web.manage, "unmute", lambda mid: removed.update(id=mid) or True)
    monkeypatch.setattr(web.manage, "list_mutes", lambda: [])
    r = client.post("/ui/alerts/unmute/3")
    assert r.status_code == 200
    assert removed == {"id": 3}
    assert "Активных правил подавления нет" in r.text


# --- Ask-бокс (RAG-оркестратор) ---
def _graph_ctx_stub():
    """Дерево влияния через radial_tree: сектор+пир, агрегат событий, фактор-сырьё."""
    from geoanalytics.api.charts import radial_tree
    branches = [
        {"label": "Банки", "css": "gn-sector", "size": 0.7, "children": [
            {"label": "VTBR", "css": "gn-peer", "size": 0.4, "url": "/ui/graph?ticker=VTBR"}]},
        {"label": "↑ 1", "css": "up", "size": 0.6, "children": [
            {"label": "0.80", "css": "up", "size": 0.8, "title": "хорошая новость",
             "url": "http://x/1"}]},
        {"label": "Brent +0.30", "css": "gn-commodity", "size": 0.3},
    ]
    return {"ticker": "SBER", "graph": radial_tree("SBER", branches), "assets": []}


def test_graph_page_renders_tree(monkeypatch):
    monkeypatch.setattr(web, "_graph_context", lambda t: _graph_ctx_stub())
    r = client.get("/ui/graph?ticker=SBER")
    assert r.status_code == 200
    assert "Банки" in r.text and "Brent +0.30" in r.text     # ветви дерева
    assert 'href="/ui/graph?ticker=VTBR"' in r.text          # пир кликабелен
    assert 'hx-trigger="every 60s"' in r.text                # автообновление (D)
    assert "хорошая новость" in r.text                       # tooltip события


def test_graph_partial_returns_svg(monkeypatch):
    monkeypatch.setattr(web, "_graph_context", lambda t: _graph_ctx_stub())
    r = client.get("/ui/partials/graph?ticker=SBER")
    assert r.status_code == 200
    assert "<svg" in r.text and "graph-zoom" in r.text
    assert "<h1>" not in r.text                              # это фрагмент, не страница


def _market_ctx_stub():
    """Большой граф через radial_layout: IMOEX → сектор → актив → событие."""
    from geoanalytics.api.charts import radial_layout
    root = {"label": "IMOEX", "children": [
        {"label": "Банки", "css": "gn-sector", "size": 0.8, "children": [
            {"label": "SBER", "css": "gn-peer", "size": 0.6, "url": "/ui/graph?ticker=SBER",
             "title": "Сбербанк · давление 0.60", "children": [
                 {"label": "0.80", "css": "up", "size": 0.8, "title": "хорошая новость",
                  "url": "http://x/1"}]}]}]}
    return {"graph": radial_layout(root), "is_market": True}


def test_market_graph_page(monkeypatch):
    monkeypatch.setattr(web, "_market_graph_context", lambda: _market_ctx_stub())
    r = client.get("/ui/graph/market")
    assert r.status_code == 200
    assert "Граф рынка" in r.text
    assert "Банки" in r.text and "SBER" in r.text          # сектор и актив
    assert 'href="/ui/graph?ticker=SBER"' in r.text         # актив кликабелен
    assert 'hx-trigger="every 60s"' in r.text               # автообновление (D)


def test_market_graph_partial(monkeypatch):
    monkeypatch.setattr(web, "_market_graph_context", lambda: _market_ctx_stub())
    r = client.get("/ui/partials/graph/market")
    assert r.status_code == 200
    assert "<svg" in r.text and "graph-zoom" in r.text
    assert "<h1>" not in r.text                              # фрагмент, не страница


def test_ask_partial_empty():
    r = client.get("/ui/partials/ask?q=")
    assert r.status_code == 200
    assert "Задайте вопрос" in r.text


def test_ask_partial_renders_result(monkeypatch):
    from geoanalytics.query.ask import AskResult
    res = AskResult(question="как дела у сбера", intent="asset", answer="Сбербанк стабилен.",
                    facts=["Сбербанк (SBER)", "RSI(14): 55"],
                    citations=[{"title": "Отчёт банка", "url": "http://x/1"}],
                    ticker="SBER", used_llm=True)
    monkeypatch.setattr(web, "ask_answer", lambda q: res)
    r = client.get("/ui/partials/ask?q=как+дела+у+сбера")
    assert r.status_code == 200
    assert "Сбербанк стабилен." in r.text
    assert 'href="/ui/asset?ticker=SBER"' in r.text       # тикер кликабелен
    assert 'href="http://x/1"' in r.text                   # цитата-ссылка
