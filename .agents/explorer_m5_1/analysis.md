# Detailed CLI Architecture & Decomposition Analysis

## 1. Executive Summary
`src/geoanalytics/cli.py` is a 2,696-line monolithic CLI module built using `typer` and `rich`. It exposes the `geo` CLI entry point, registered in `pyproject.toml` as `geo = "geoanalytics.cli:app"`.

The file currently defines:
- A root Typer app instance (`app`) with a global callback (`_init()`) for log configuration.
- 6 sub-Typer applications (`portfolio_app`, `fundamentals_app`, `segments_app`, `futures_intraday_app`, `futures_depth_app`, `db_app`).
- 49 top-level commands mounted via `@app.command()`.
- Shared Rich UI helpers (`console = Console()`, `_rich_link`, `_fmt`).

All 1,243 baseline unit tests (`pytest tests/`) are currently passing 100%.

To satisfy the constraint that no single file exceeds 600 lines while preserving 100% backward compatibility, `src/geoanalytics/cli.py` should be decomposed into a modular package under `src/geoanalytics/cli/`.

---

## 2. CLI Entry Point & Invocation Analysis
- **Entry Point Definition**: `pyproject.toml` line 68:
  ```toml
  [project.scripts]
  geo = "geoanalytics.cli:app"
  ```
- **Execution Context**:
  - Control script `geo-ctl.sh` invokes `.venv/bin/geo serve` to launch the web dashboard server.
  - Users and system scripts invoke `geo <command>` directly via Typer dispatcher `geoanalytics.cli:app`.
- **Contract Requirement**:
  - `src/geoanalytics/cli.py` must continue to export the main `app` object (`from geoanalytics.cli import app` or `geoanalytics.cli:app`), maintaining exact command signatures, parameter names, option flags, defaults, and rich formatting.

---

## 3. Current Command Inventory & Grouping

### 3.1 Shared State & Helpers
- `app`: `typer.Typer(help="geoanalytics — аналитика экономики и геополитики (рынок РФ).", no_args_is_help=True)`
- `console`: `rich.console.Console()`
- `_init()`: `@app.callback()` initializing logging via `configure_logging(get_settings().log_level)`
- Helpers: `_rich_link(text, url)`, `_fmt(v, spec, pct)`

### 3.2 Sub-Typer Applications (Grouped Subcommands)
1. **`portfolio_app`** (`app.add_typer(portfolio_app, name="portfolio")`):
   - `@portfolio_app.callback()` -> `portfolio_main(ctx)` (default overview)
   - `add` (`portfolio_add`)
   - `remove` (`portfolio_remove`)
   - `cash` (`portfolio_cash`)
   - `snapshot` (`portfolio_snapshot`)
2. **`fundamentals_app`** (`app.add_typer(fundamentals_app, name="fundamentals")`):
   - `add` (`fundamentals_add`)
   - `scrape` (`fundamentals_scrape`)
   - `list` (`fundamentals_list`)
3. **`segments_app`** (`app.add_typer(segments_app, name="segments")`):
   - `add` (`segments_add`)
   - `list` (`segments_list`)
4. **`futures_intraday_app`** (`app.add_typer(futures_intraday_app, name="futures-intraday")`):
   - `backfill`, `continuous`, `accumulate`, `simulate`, `log-decisions`, `decisions`, `train-policy`, `evaluate`, `models`, `pbo`, `drift`, `paper`, `risk-status`, `paper-reset`, `resume`, `paper-status`, `track-record` (17 subcommands)
5. **`futures_depth_app`** (`app.add_typer(futures_depth_app, name="futures-depth")`):
   - `capture`, `status` (2 subcommands)
6. **`db_app`** (`app.add_typer(db_app, name="db")`):
   - `upgrade`, `seed` (2 subcommands)

### 3.3 Top-Level Commands List (49 commands)
1. Data Ingestion & Pipeline: `sources`, `ingest`, `news-backfill`, `process`, `relink`, `reprocess`, `reconcile-impacts`, `rescore`, `prune`, `pipeline`
2. Batch NLP Model Re-scoring: `reaspect`, `retemporal`, `refactuality`, `renumeric`, `reforecast`
3. Market Intelligence & Analytics: `news`, `digest`, `forecasts`, `stories`, `calendar`, `outcomes`, `continuous-eval`, `active-learn`, `sentiment-index`, `reliability`, `significance-audit`, `event-study`, `alert-outcomes`, `events`
4. Financial Assets & Factor Models: `attribution`, `graph`, `regime`, `pressure`, `sentiment-trend`, `backfill`, `context`, `asset`, `asset-context-accumulate`, `factor-scores`, `candles`, `whatif`
5. Technical Strategies & Backtesting: `backtest`, `walkforward`
6. System Services & Operations: `health`, `alerts`, `run-scheduler`, `run-futrader`, `run-bot`, `serve`

---

## 4. Proposed Submodule Decomposition Plan

We propose creating a package `src/geoanalytics/cli/` containing 8 clean submodules plus the thin entry point `src/geoanalytics/cli.py`.

```
src/geoanalytics/
├── cli.py                    (~60 lines) — Thin entry point dispatcher importing and registering submodules
└── cli/
    ├── __init__.py           (~15 lines) — Package initializer
    ├── common.py             (~45 lines) — Shared Typer app instance, Console, logging callback & formatting helpers
    ├── pipeline.py           (~285 lines) — Data ingestion, raw processing, and NLP re-scoring commands
    ├── nlp.py                (~470 lines) — News summaries, digests, event studies, intelligence & outcomes
    ├── market.py             (~550 lines) — Asset reporting, factor models, sentiment trends & scenario analysis
    ├── backtest.py           (~170 lines) — Strategy backtesting and walk-forward validation
    ├── portfolio.py          (~290 lines) — Portfolio management, company fundamentals & revenue segments
    ├── futrader.py           (~525 lines) — Track 2 FORTS futures intraday, depth capture & paper trading
    └── services.py           (~140 lines) — System services, alerts, database migrations & API server runner
```

### Line Count Budget Breakdown
| Submodule | Responsibilities / Included Commands | Est. Lines | Status (<600) |
|---|---|---|---|
| `cli.py` | Top-level dispatcher exposing `app` for `geoanalytics.cli:app` | ~60 | PASS |
| `cli/common.py` | Shared `app` or sub-app base, `console`, `_init`, `_rich_link`, `_fmt` | ~45 | PASS |
| `cli/pipeline.py` | `sources`, `ingest`, `news-backfill`, `process`, `relink`, `reconcile-impacts`, `rescore`, `prune`, `reaspect`, `retemporal`, `reprocess`, `refactuality`, `renumeric`, `reforecast`, `pipeline` | ~285 | PASS |
| `cli/nlp.py` | `news`, `digest`, `forecasts`, `stories`, `calendar`, `outcomes`, `continuous-eval`, `active-learn`, `sentiment-index`, `reliability`, `significance-audit`, `event-study`, `alert-outcomes`, `events` | ~470 | PASS |
| `cli/market.py` | `attribution`, `graph`, `regime`, `pressure`, `sentiment-trend`, `backfill`, `context`, `asset`, `asset-context-accumulate`, `factor-scores`, `candles`, `whatif` | ~550 | PASS |
| `cli/backtest.py` | `backtest`, `walkforward` | ~170 | PASS |
| `cli/portfolio.py` | Sub-typers `portfolio_app`, `fundamentals_app`, `segments_app` | ~290 | PASS |
| `cli/futrader.py` | Sub-typers `futures_intraday_app`, `futures_depth_app` | ~525 | PASS |
| `cli/services.py` | `health`, `alerts`, `db_app`, `run-scheduler`, `run-futrader`, `run-bot`, `serve` | ~140 | PASS |

---

## 5. Architectural Implementation Pattern

To avoid circular imports and keep Typer command routing clean:

1. `src/geoanalytics/cli/common.py`:
   - Instantiates `app = typer.Typer(...)` and `console = Console()`.
   - Defines global callback `@app.callback() def _init()`.
   - Defines shared formatting helpers (`_rich_link`, `_fmt`).

2. Submodules (`pipeline.py`, `nlp.py`, `market.py`, `backtest.py`, `services.py`):
   - Import `app` and `console` from `geoanalytics.cli.common`.
   - Register commands directly using `@app.command()`.

3. Grouped Submodules (`portfolio.py`, `futrader.py`, `services.py`):
   - Instantiate their sub-Typer apps (`portfolio_app`, `futures_intraday_app`, `db_app`, etc.).
   - Mount them onto `app` using `app.add_typer(...)`.

4. `src/geoanalytics/cli.py`:
   - Imports `app` from `geoanalytics.cli.common`.
   - Imports all submodules (`geoanalytics.cli.pipeline`, `geoanalytics.cli.nlp`, etc.) so that all command decorators execute and register commands.
   - Re-exports `app` as `geoanalytics.cli:app` for entry point compatibility.

---

## 6. Baseline Verification Results
- Command: `source .venv/bin/activate && pytest tests/`
- Result: 1,243 passed in 58.4s. Zero failures.
- CLI Entry Point: verified `pyproject.toml` `[project.scripts] geo = "geoanalytics.cli:app"`.
- Control Script: verified `geo-ctl.sh` invokes `.venv/bin/geo serve`.
