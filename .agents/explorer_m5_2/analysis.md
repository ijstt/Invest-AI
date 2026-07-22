# Milestone 5 Analysis Report: CLI Dependency Graph & Modularization Strategy

## Executive Summary
This report presents a thorough investigation of `src/geoanalytics/cli.py` (2,696 lines) for Milestone 5 (CLI Modularization). The CLI contains 81 command functions across 7 Typer app instances. Baseline test execution confirms that 1,243 unit tests currently pass (100%).

We outline a modular architecture that splits `src/geoanalytics/cli.py` into 10 domain-specific submodules under `src/geoanalytics/cli/` plus a lightweight dispatcher in `src/geoanalytics/cli.py` (~75 lines), ensuring no file exceeds 600 lines.

---

## 1. Catalog of `src/geoanalytics/cli.py`

### 1.1 Structural Overview
- **File size**: 2,696 lines
- **AST Nodes**: 106 top-level nodes (7 import statements, 83 functions, 8 top-level assignments/app instantiations)
- **Typer App Instances**:
  1. `app = typer.Typer(...)` (Main CLI app, L23)
  2. `portfolio_app = typer.Typer(...)` (L1404) -> `app.add_typer(portfolio_app, name='portfolio')`
  3. `fundamentals_app = typer.Typer(...)` (L1411) -> `app.add_typer(fundamentals_app, name='fundamentals')`
  4. `segments_app = typer.Typer(...)` (L1525) -> `app.add_typer(segments_app, name='segments')`
  5. `futures_intraday_app = typer.Typer(...)` (L1594) -> `app.add_typer(futures_intraday_app, name='futures-intraday')`
  6. `futures_depth_app = typer.Typer(...)` (L1598) -> `app.add_typer(futures_depth_app, name='futures-depth')`
  7. `db_app = typer.Typer(...)` (L2618) -> `app.add_typer(db_app, name='db')`

### 1.2 Import Inventory
- **Top-Level External / Core Imports**:
  - `from __future__ import annotations`
  - `import typer`
  - `from rich.console import Console`
  - `from rich.panel import Panel`
  - `from rich.table import Table`
  - `from config.settings import get_settings`
  - `from geoanalytics.core.logging import configure_logging`
- **Inline / Subcommand Imports**: 180 inline import statements across command function bodies (including `alembic`, `uvicorn`, `sqlalchemy.select`, `json`, `datetime`, `pathlib`, and domain logic modules in `geoanalytics.*`).

### 1.3 Helper Functions & Formatters
- `console = Console()` (Rich console instance used across 39 commands for table and panel output)
- `_init(verbose: bool)` (Typer callback for verbose logging setup, L31-33)
- `_rich_link(title: str, url: str) -> str` (Formatting clickable Rich title/URL string, L1279-1281)
- `_fmt(val: float | None, fmt: str = ".4f") -> str` (Safe float formatter with "-" fallback, L1910-1913)

---

## 2. Shared Utilities & Circular Import Prevention (`common.py`)

### 2.1 Placement in `src/geoanalytics/cli/common.py`
To prevent circular imports, all shared formatters, console instances, and common Typer callbacks will be placed in `src/geoanalytics/cli/common.py`:
- `console = Console()`
- `_rich_link(title: str, url: str) -> str`
- `_fmt(val: float | None, fmt: str = ".4f") -> str`
- `_init` callback / logging setup

### 2.2 Import Contract & Dependency Graph
```
                        [ config.settings, rich, typer ]
                                       │
                                       ▼
                       src/geoanalytics/cli/common.py
                                       │
            ┌──────────────────────────┼──────────────────────────┐
            ▼                          ▼                          ▼
   cli/ingest.py              cli/analytics.py            cli/futures_intraday.py
    (and others)                (and others)               (and others)
            │                          │                          │
            └──────────────────────────┼──────────────────────────┘
                                       ▼
                          src/geoanalytics/cli.py
                       (App Dispatcher <100 lines)
```
- **Unidirectional Imports**: `common.py` imports only core standard/third-party packages and never imports subcommand modules.
- Domain submodules import `console`, `_rich_link`, `_fmt` from `geoanalytics.cli.common`.
- `src/geoanalytics/cli.py` imports sub-apps and commands from domain submodules and registers them on `app`.
- **Result**: 0 circular import dependencies.

---

## 3. Subcommand Domain Mapping & Line Count Budget

The 81 command functions are mapped to 10 domain submodules:

| Target Submodule | Sub-App / Commands Included | Function Count | Est. Line Count | Status (<600 Limit) |
|---|---|---|---|---|
| `cli/common.py` | `console`, `_rich_link`, `_fmt`, shared setup | N/A | ~30 lines | PASS |
| `cli/ingest.py` | `sources`, `ingest`, `news_backfill`, `news`, `digest` | 5 | ~150 lines | PASS |
| `cli/nlp.py` | `process`, `relink`, `reconcile_impacts_cmd`, `rescore`, `prune`, `health`, `reaspect`, `retemporal`, `reprocess`, `refactuality`, `renumeric`, `reforecast` | 12 | ~210 lines | PASS |
| `cli/analytics.py` | `forecasts`, `stories`, `calendar`, `outcomes`, `continuous_eval`, `active_learn`, `sentiment_index`, `reliability`, `significance_audit`, `event_study`, `attribution`, `graph`, `regime`, `pressure`, `sentiment_trend`, `alert_outcomes`, `pipeline` | 17 | ~550 lines | PASS |
| `cli/alerts.py` | `backfill`, `context`, `events`, `alerts` | 4 | ~135 lines | PASS |
| `cli/backtest.py` | `backtest`, `walkforward`, `asset`, `asset_context_accumulate`, `factor_scores`, `candles`, `whatif` | 7 | ~480 lines | PASS |
| `cli/fundamentals.py` | `fundamentals_app` (`add`, `scrape`, `list`), `segments_app` (`add`, `list`) | 5 | ~170 lines | PASS |
| `cli/futures_depth.py` | `futures_depth_app` (`capture`, `status`) | 2 | ~40 lines | PASS |
| `cli/futures_intraday.py` | `futures_intraday_app` (`backfill`, `continuous`, `accumulate`, `simulate`, `log-decisions`, `decisions`, `train-policy`, `evaluate`, `models`, `pbo`, `drift`, `paper`, `risk-status`, `paper-reset`, `resume`, `paper-status`, `track-record`) | 17 | ~580 lines | PASS |
| `cli/portfolio.py` | `portfolio_app` (`portfolio_main`, `add`, `remove`, `cash`, `snapshot`) | 5 | ~160 lines | PASS |
| `cli/system.py` | `db_app` (`upgrade`, `seed`), `run_scheduler`, `run_futrader`, `run_bot`, `serve` | 6 | ~70 lines | PASS |
| `cli.py` (Dispatcher) | Main `app` instantiation, sub-app registration, entry point | N/A | ~75 lines | PASS |

---

## 4. Entry Point Dispatcher Specification (`src/geoanalytics/cli.py`)

`src/geoanalytics/cli.py` will be refactored into a concise dispatcher:
```python
from __future__ import annotations
import typer

from geoanalytics.cli.common import _init
from geoanalytics.cli.ingest import register_ingest_commands
from geoanalytics.cli.nlp import register_nlp_commands
from geoanalytics.cli.analytics import register_analytics_commands
from geoanalytics.cli.alerts import register_alerts_commands
from geoanalytics.cli.backtest import register_backtest_commands
from geoanalytics.cli.fundamentals import fundamentals_app, segments_app
from geoanalytics.cli.futures_depth import futures_depth_app
from geoanalytics.cli.futures_intraday import futures_intraday_app
from geoanalytics.cli.portfolio import portfolio_app
from geoanalytics.cli.system import db_app, register_system_commands

app = typer.Typer(help="geoanalytics — аналитика экономики и геополитики (рынок РФ).", no_args_is_help=True)
app.callback()(_init)

# Register sub-apps
app.add_typer(portfolio_app, name="portfolio")
app.add_typer(fundamentals_app, name="fundamentals")
app.add_typer(segments_app, name="segments")
app.add_typer(futures_intraday_app, name="futures-intraday")
app.add_typer(futures_depth_app, name="futures-depth")
app.add_typer(db_app, name="db")

# Register top-level commands from domain modules
register_ingest_commands(app)
register_nlp_commands(app)
register_analytics_commands(app)
register_alerts_commands(app)
register_backtest_commands(app)
register_system_commands(app)

if __name__ == "__main__":
    app()
```

---

## 5. Baseline Pytest Verification Result

- **Command**: `source .venv/bin/activate && pytest tests/`
- **Timestamp**: 2026-07-22T19:11:31Z
- **Passed**: 1243 / 1243 tests (100%)
- **Status**: Baseline fully verified green.
