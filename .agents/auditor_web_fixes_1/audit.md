## Forensic Audit Report

**Work Product**: Web Fixes (templates, router logic, and tests)
**Profile**: General Project
**Verdict**: CLEAN

### Phase Results
- **Hardcoded output detection**: PASS — Checked `src/geoanalytics/api/web.py`, templates, and `tests/test_web.py` for any hardcoded test expectations or dummy results. No shortcuts or bypassing hardcoded strings were found; template logic is fully dynamic.
- **Facade detection**: PASS — Verified that `web.py` features a real HTMX endpoint implementation for empty tickers and correctly updates template rendering. Datalist autocomplete dropdown is fully dynamic, and the portfolio correlation layout renders actual model correlation results.
- **Pre-populated artifact detection**: PASS — Checked the repository for pre-populated logs, result files, or fabricated test output. Only standard package files under `.venv` and actual template files exist.
- **Build and run**: PASS — Successfully ran the web test suite (`.venv/bin/pytest tests/test_web.py`). All 42 tests executed and passed without issues.
- **Output verification**: PASS — Verified HTML structure and formatting variables. Assertions match the actual template output (e.g. `unreal_pct` and `duration_bars` fallbacks, `Корреляции холдингов` section, and datalist autocompletion).
- **Dependency audit**: PASS — No core logic or target deliverables are delegated to prohibited third-party libraries.

### Evidence

#### 1. Verbose Pytest Output
```
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.3, pluggy-1.6.0 -- /home/ijstt/News/.venv/bin/python3
cachedir: .pytest_cache
rootdir: /home/ijstt/News
configfile: pyproject.toml
plugins: respx-0.23.1, asyncio-1.4.0, anyio-4.13.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collecting 0 items                                                             collected 42 items

tests/test_web.py::test_sparkline_basic PASSED                           [  2%]
tests/test_web.py::test_sparkline_down_flag PASSED                       [  4%]
tests/test_web.py::test_sparkline_insufficient PASSED                    [  7%]
tests/test_web.py::test_dashboard PASSED                                 [  9%]
tests/test_web.py::test_asset_page PASSED                                [ 11%]
tests/test_web.py::test_asset_page_shows_graph_panel PASSED              [ 14%]
tests/test_web.py::test_portfolio_page_empty PASSED                      [ 16%]
tests/test_web.py::test_portfolio_page_with_positions PASSED             [ 19%]
tests/test_web.py::test_portfolio_page_quality_panels PASSED             [ 21%]
tests/test_web.py::test_portfolio_add_form PASSED                        [ 23%]
tests/test_web.py::test_portfolio_add_form_swallows_bad_input PASSED     [ 26%]
tests/test_web.py::test_portfolio_remove_form PASSED                     [ 28%]
tests/test_web.py::test_portfolio_cash_row_delete_targets_cash_endpoint PASSED [ 30%]
tests/test_web.py::test_portfolio_cash_form_zero_amount_removes_balance PASSED [ 33%]
tests/test_web.py::test_unhandled_exception_returns_html_500 PASSED      [ 35%]
tests/test_web.py::test_cached_ttl PASSED                                [ 38%]
tests/test_web.py::test_factors_page PASSED                              [ 40%]
tests/test_web.py::test_asset_partial_empty_ticker PASSED                [ 42%]
tests/test_web.py::test_indicators_partial_period_toggle PASSED          [ 45%]
tests/test_web.py::test_indicators_partial_empty_ticker PASSED           [ 47%]
tests/test_web.py::test_backtest_page PASSED                             [ 50%]
tests/test_web.py::test_backtest_partial_error PASSED                    [ 52%]
tests/test_web.py::test_backtest_form_lists_strategies PASSED            [ 54%]
tests/test_web.py::test_track2_page PASSED                               [ 57%]
tests/test_web.py::test_track2_page_empty PASSED                         [ 59%]
tests/test_web.py::test_track2_partial_halted PASSED                     [ 61%]
tests/test_web.py::test_news_partial PASSED                              [ 64%]
tests/test_web.py::test_assets_endpoint PASSED                           [ 66%]
tests/test_web.py::test_asset_form_has_datalist PASSED                   [ 69%]
tests/test_web.py::test_asset_chart_partial_candles PASSED               [ 71%]
tests/test_web.py::test_chart_indicator_toggles PASSED                   [ 73%]
tests/test_web.py::test_alerts_page PASSED                               [ 76%]
tests/test_web.py::test_alerts_partial_filtered PASSED                   [ 78%]
tests/test_web.py::test_alert_ack_swaps_row PASSED                       [ 80%]
tests/test_web.py::test_alert_mute_renders_panel PASSED                  [ 83%]
tests/test_web.py::test_alert_unmute_renders_panel PASSED                [ 85%]
tests/test_web.py::test_graph_page_renders_tree PASSED                   [ 88%]
tests/test_web.py::test_graph_partial_returns_svg PASSED                 [ 90%]
tests/test_web.py::test_market_graph_page PASSED                         [ 92%]
tests/test_web.py::test_market_graph_partial PASSED                      [ 95%]
tests/test_web.py::test_ask_partial_empty PASSED                         [ 97%]
tests/test_web.py::test_ask_partial_renders_result PASSED                [100%]
======================== 42 passed, 1 warning in 8.83s =========================
```

#### 2. Implemented Code Changes Diffs
```diff
diff --git a/src/geoanalytics/api/templates/_track2.html b/src/geoanalytics/api/templates/_track2.html
index 86f75c5..283d0c7 100644
--- a/src/geoanalytics/api/templates/_track2.html
+++ b/src/geoanalytics/api/templates/_track2.html
@@ -270,12 +270,12 @@
         <td class="num {{ 'up' if p.net_qty >= 0 else 'down' }}">{{ "%+d"|format(p.net_qty) }}</td>
         <td class="num">{{ "%.2f"|format(p.avg_price) if p.avg_price is not none else "—" }}</td>
         <td class="num">{{ "%.2f"|format(p.last_price) if p.last_price is not none else "—" }}</td>
-        <td class="num {{ 'up' if (p.unreal_pct or 0) >= 0 else 'down' }}">
-          {% if p.unreal_pct is not none %}{{ "%+.2f"|format(p.unreal_pct) }}%{% else %}—{% endif %}
+        <td class="num {{ 'up' if (p.unreal_pct is defined and p.unreal_pct or 0) >= 0 else 'down' }}">
+          {% if p.unreal_pct is defined and p.unreal_pct is not none %}{{ "%+.2f"|format(p.unreal_pct) }}%{% else %}—{% endif %}
         </td>
         <td class="num {{ 'up' if p.realized_pnl >= 0 else 'down' }}">{{ "{:+,.0f}".format(p.realized_pnl) }}</td>
         <td>
-          {% if p.duration_bars is not none %}
+          {% if p.duration_bars is defined and p.duration_bars is not none %}
           <div style="display:flex; align-items:center; gap:6px;">
             <div class="hold-bar-wrap">
               <div class="hold-bar-fill" style="width:{{ [p.duration_bars / 12 * 100, 100] | min }}%;"></div>
diff --git a/src/geoanalytics/api/templates/asset.html b/src/geoanalytics/api/templates/asset.html
index 02a2de0..d511538 100644
--- a/src/geoanalytics/api/templates/asset.html
+++ b/src/geoanalytics/api/templates/asset.html
@@ -6,13 +6,13 @@
   <form action="/ui/asset" method="get"
         hx-get="/ui/partials/asset" hx-target="#asset-result" hx-push-url="true"
         style="display: flex; gap: 12px; align-items: center; margin: 0;">
-    <select name="ticker" onchange="this.form.dispatchEvent(new Event('submit', {cancelable: true}))" 
-            style="min-width: 240px; padding: 10px 14px; border-radius: 8px; border: 1px solid var(--line); background: var(--panel2); color: var(--fg); font-family: var(--font-mono); font-size: 13px; cursor: pointer;">
-      {% for a in assets or [] %}
-      <option value="{{ a.ticker }}" {% if ticker == a.ticker %}selected{% endif %}>{{ a.ticker }} — {{ a.name }}</option>
-      {% endfor %}
-    </select>
-    <button type="submit" style="display: none;">Показать</button>
+    <input type="text" name="ticker" placeholder="Тикер, напр. IMOEX" list="tickers"
+           value="{{ ticker or '' }}" autocomplete="off" autofocus
+           style="min-width: 240px; padding: 10px 14px; border-radius: 8px; border: 1px solid var(--line); background: var(--panel2); color: var(--fg); font-family: var(--font-mono); font-size: 13px;">
+    <datalist id="tickers">
+      {% for a in assets or [] %}<option value="{{ a.ticker }}">{{ a.name }}</option>{% endfor %}
+    </datalist>
+    <button type="submit" style="padding: 10px 18px; border-radius: 8px; border: 1px solid var(--line); background: var(--panel2); color: var(--fg); font-size: 13px; cursor: pointer;">Показать</button>
     <span class="htmx-indicator muted">загрузка…</span>
   </form>
 </div>
diff --git a/src/geoanalytics/api/templates/portfolio.html b/src/geoanalytics/api/templates/portfolio.html
index 225b8a4..7cd9119 100644
--- a/src/geoanalytics/api/templates/portfolio.html
+++ b/src/geoanalytics/api/templates/portfolio.html
@@ -241,6 +241,15 @@
     {% endfor %}
   </div>
   {% endif %}
+
+  {% if correlations %}
+  <div class="panel">
+    <h2>Корреляции холдингов</h2>
+    {% for c in correlations %}
+    <div class="metric"><span class="k">{{ c.pair }}</span><span class="v {{ 'up' if c.r >= 0 else 'down' }}">{{ "%+.2f"|format(c.r) }}</span></div>
+    {% endfor %}
+  </div>
+  {% endif %}
 </div>
 </div>
 <div class="grid" style="margin-top:18px;">
diff --git a/src/geoanalytics/api/web.py b/src/geoanalytics/api/web.py
index bd80987..12eee1d 100644
--- a/src/geoanalytics/api/web.py
+++ b/src/geoanalytics/api/web.py
@@ -922,7 +922,7 @@ def asset_page(request: Request, ticker: str | None = None):
 def asset_partial(request: Request, ticker: str = ""):
     """HTMX-фрагмент с отчётом по активу."""
     if not ticker or not ticker.strip():
-        ticker = "IMOEX"
+        return HTMLResponse("<p class=\"muted\">Введите тикер</p>")
     return templates.TemplateResponse(request, "_asset_result.html", _asset_context(ticker))
diff --git a/tests/test_web.py b/tests/test_web.py
index 1e01831..d1b94f6 100644
--- a/tests/test_web.py
+++ b/tests/test_web.py
@@ -345,7 +345,8 @@ def _track2_ctx_populated():
         "limits": RiskLimits(), "halt": None,
         "value_chart": sparkline([100000.0, 101000.0, 99500.0, 102500.0]),
         "positions": [{"asset_code": "BR", "interval": "1h", "source": "rsi", "net_qty": 1,
-                       "avg_price": 70.5, "last_price": 71.2, "realized_pnl": 0.0}],
+                       "avg_price": 70.5, "last_price": 71.2, "realized_pnl": 0.0,
+                       "unreal_pct": 0.99, "duration_bars": 2}],
         "trades": [{"ts": datetime(2026, 6, 20, 14, 0), "asset_code": "BR", "source": "rsi",
                     "action": "buy", "signed_qty": 1, "price": 70.5, "p_win": 0.61,
                     "realized_pnl": None, "reason": "entry", "conviction": 0.42}],
```
