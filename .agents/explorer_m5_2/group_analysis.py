import ast

cli_path = "/home/ijstt/News/src/geoanalytics/cli.py"

with open(cli_path, "r", encoding="utf-8") as f:
    source = f.read()

tree = ast.parse(source, filename=cli_path)

commands = []

for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        name = node.name
        start = node.lineno
        end = getattr(node, 'end_lineno', start)
        length = end - start + 1
        
        cmd_app = None
        cmd_name = name
        
        for dec in node.decorator_list:
            dec_code = ast.unparse(dec)
            if "command" in dec_code or "callback" in dec_code:
                if dec_code.startswith("app.command"):
                    cmd_app = "app"
                elif dec_code.startswith("app.callback"):
                    cmd_app = "app_callback"
                elif dec_code.startswith("portfolio_app"):
                    cmd_app = "portfolio_app"
                elif dec_code.startswith("fundamentals_app"):
                    cmd_app = "fundamentals_app"
                elif dec_code.startswith("segments_app"):
                    cmd_app = "segments_app"
                elif dec_code.startswith("futures_intraday_app"):
                    cmd_app = "futures_intraday_app"
                elif dec_code.startswith("futures_depth_app"):
                    cmd_app = "futures_depth_app"
                elif dec_code.startswith("db_app"):
                    cmd_app = "db_app"
                else:
                    cmd_app = dec_code
        
        commands.append({
            "name": name,
            "cmd_app": cmd_app,
            "start": start,
            "end": end,
            "length": length
        })

# Proposed modules mapping:
# 1. common.py - Console, _rich_link, _fmt, etc.
# 2. ingest.py - sources, ingest, news_backfill, news, digest (total ~132 lines)
# 3. nlp.py - process, relink, reconcile_impacts_cmd, rescore, prune, health, reaspect, retemporal, reprocess, refactuality, renumeric, reforecast (total ~180 lines)
# 4. analytics.py - forecasts, stories, calendar, outcomes, continuous_eval, active_learn, sentiment_index, reliability, significance_audit, event_study, attribution, graph, regime, pressure, sentiment_trend, alert_outcomes, pipeline (total ~480 lines)
# 5. alerts.py - backfill, context, events, alerts (total ~120 lines)
# 6. backtest.py - backtest, walkforward, asset, asset_context_accumulate, factor_scores, candles, whatif (total ~440 lines)
# 7. fundamentals.py - fundamentals_app (add, scrape, list), segments_app (add, list) (total ~160 lines)
# 8. futures.py - futures_depth_app (capture, status), futures_intraday_app (backfill, continuous, accumulate, simulate, log-decisions, decisions, train-policy, evaluate, models, pbo, drift, paper, risk-status, paper-reset, resume, paper-status, track-record) (total ~620 lines -> wait! let's check if futures.py can be split or if 620 lines can be trimmed down or if futures.py needs futures_depth vs futures_intraday!)
# 9. portfolio.py - portfolio_app (portfolio_main, add, remove, cash, snapshot) (total ~150 lines)
# 10. system.py - db_app (upgrade, seed), run_scheduler, run_futrader, run_bot, serve (total ~60 lines)

groups = {
    "ingest": ["sources", "ingest", "news_backfill", "news", "digest"],
    "nlp": ["process", "relink", "reconcile_impacts_cmd", "rescore", "prune", "health", "reaspect", "retemporal", "reprocess", "refactuality", "renumeric", "reforecast"],
    "analytics": ["forecasts", "stories", "calendar", "outcomes", "continuous_eval", "active_learn", "sentiment_index", "reliability", "significance_audit", "event_study", "attribution", "graph", "regime", "pressure", "sentiment_trend", "alert_outcomes", "pipeline"],
    "alerts": ["backfill", "context", "events", "alerts"],
    "backtest": ["backtest", "walkforward", "asset", "asset_context_accumulate", "factor_scores", "candles", "whatif"],
    "fundamentals": ["fundamentals_add", "fundamentals_scrape", "fundamentals_list", "segments_add", "segments_list"],
    "futures_depth": ["futures_depth_capture", "futures_depth_status"],
    "futures_intraday": [
        "futures_intraday_backfill", "futures_intraday_continuous", "futures_intraday_accumulate",
        "futures_intraday_simulate", "futures_intraday_log_decisions", "futures_intraday_decisions",
        "futures_intraday_train_policy", "futures_intraday_evaluate", "futures_intraday_models",
        "futures_intraday_pbo", "futures_intraday_drift", "futures_intraday_paper",
        "futures_intraday_risk_status", "futures_intraday_paper_reset", "futures_intraday_resume",
        "futures_intraday_paper_status", "futures_intraday_track_record"
    ],
    "portfolio": ["portfolio_main", "portfolio_add", "portfolio_remove", "portfolio_cash", "portfolio_snapshot"],
    "system": ["db_upgrade", "db_seed", "run_scheduler", "run_futrader", "run_bot", "serve"]
}

fn_len_map = {c['name']: c['length'] for c in commands}

print("=== GROUP LINE COUNT ANALYSIS ===")
for grp, fns in groups.items():
    total_len = sum(fn_len_map.get(f, 0) for f in fns)
    print(f"Group '{grp:18s}': {len(fns):2d} functions, ~{total_len:4d} lines of code")

unassigned = set(fn_len_map.keys()) - set(sum(groups.values(), [])) - {"_rich_link", "_fmt", "_init"}
print(f"\nUnassigned functions: {unassigned}")
