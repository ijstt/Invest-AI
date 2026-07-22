# Milestone 5 Analysis: CLI Modularization, Raspberry Pi Integration & Test Coverage

## Executive Summary
This report analyzes `src/geoanalytics/cli.py` (currently a monolithic file of 2,696 lines), its invocations across system control scripts (`./geo-ctl.sh`), Raspberry Pi deployment scripts (`deploy/pi/*`), test coverage in `tests/`, and outlines the backward compatibility rules required to modularize `geoanalytics.cli` into a clean package hierarchy (`src/geoanalytics/cli/`).

---

## 1. Script & Raspberry Pi Integration (`./geo-ctl.sh` and `deploy/pi/*`)

### Direct CLI Command Invocations
An exhaustive audit of control scripts, systemd unit files, setup/preflight scripts, and documentation revealed the following exact CLI command invocations:

| Script / File | Line Number | Exact Command Invocation | Purpose / Context |
|---|---|---|---|
| `./geo-ctl.sh` | Line 46, 88 | `.venv/bin/geo serve` | Manual startup fallback when systemd is not present |
| `deploy/pi/geo-alerts.service` | Line 13 | `ExecStart=/home/pi/News/.venv/bin/geo run-scheduler` | Scheduler service for data ingestion & NLP pipeline |
| `deploy/pi/geo-dashboard.service` | Line 15 | `ExecStart=%h/News/.venv/bin/geo serve --host 0.0.0.0 --port 8800` | REST API / Web Dashboard server |
| `deploy/pi/geo-depth.service` | Line 12 | `ExecStart=%h/News/.venv/bin/geo futures-depth capture --interval-sec 5` | L2 orderbook depth capturing service |
| `deploy/pi/geo-futrader.service` | Line 13 | `ExecStart=%h/News/.venv/bin/geo run-futrader` | FORTS futures autonomous trading loop |
| `deploy/pi/preflight.sh` | Line 13 | `PYTHONPATH="$PWD" .venv/bin/geo run-futrader --help` | Environment preflight check before enabling service |

### Key System Integration Observations
1. **Critical Pi Execution Targets**: The system relies on four systemd services running via the `geo` CLI entry point on Raspberry Pi: `geo serve`, `geo run-scheduler`, `geo futures-depth capture`, and `geo run-futrader`.
2. **Setup Verification**: `deploy/pi/preflight.sh` validates module loading using `geo run-futrader --help`.
3. **No Flag/Subcommand Collisions**: Sub-apps (`futures-depth`, `db`, `portfolio`, `fundamentals`, `segments`, `futures-intraday`) use Typer nested command groups and must retain their exact registration names in the refactored CLI dispatcher.

---

## 2. Direct CLI Command Testing & Live System Status

Direct tests were executed against the codebase to verify existing functionality before refactoring.

### Test Results
1. **System Control Status (`./geo-ctl.sh status`)**:
   - Status command output confirmed live communication with Pi host `192.168.0.114`.
   - `/health` returned `{"status":"ok","sources":11}`.
   - Pi services (`geo-futrader`, `geo-depth`, `geo-dashboard`) reported `active`.
   - Ollama container running locally.

2. **CLI Entry Point (`.venv/bin/geo --help` with `PYTHONPATH=src:.`)**:
   - Successfully loaded `geoanalytics.cli:app`.
   - Executed and rendered full Rich-formatted help page displaying all 40+ root commands and 6 sub-apps (`db`, `futures-depth`, `futures-intraday`, `portfolio`, `fundamentals`, `segments`).

3. **Full Pytest Suite (`PYTHONPATH=src:. .venv/bin/pytest tests/`)**:
   - Result: `1243 passed, 2 warnings in 49.04s`.
   - 100% pass rate across all 97 test files.

---

## 3. Test Coverage & Monkeypatch Analysis (`tests/`)

### Key Findings
1. **Direct Imports**: Exactly **0** test files import `geoanalytics.cli` directly.
2. **Monkeypatched Functions**: Exactly **0** monkeypatches target functions or variables in `geoanalytics.cli`.
3. **Architectural Rationale**: `src/geoanalytics/cli.py` serves as a CLI presentation wrapper over underlying domain modules (such as `geoanalytics.alerts`, `geoanalytics.api`, `geoanalytics.orchestration`, `geoanalytics.analytics`, `geoanalytics.nlp`, `geoanalytics.query`, `geoanalytics.storage`). Unit tests test these core domain modules directly rather than driving the CLI wrapper via `CliRunner`.
4. **Impact Assessment for Refactoring**: Reorganizing `src/geoanalytics/cli.py` into a modular package `src/geoanalytics/cli/` carries **zero risk** of breaking unit test imports or monkeypatches in `tests/`.

---

## 4. Backward Compatibility & Modularization Strategy

### 4.1 Entry Point Contract (`pyproject.toml`)
The project configuration defines:
```toml
[project.scripts]
geo = "geoanalytics.cli:app"
```
When `geoanalytics.cli` is converted from a single module (`cli.py`) into a package directory (`cli/__init__.py`), Python resolves `geoanalytics.cli` to `src/geoanalytics/cli/__init__.py`.

### 4.2 Required Re-exports & Aliases
To guarantee 100% backward compatibility:
1. `src/geoanalytics/cli/__init__.py` must export the root Typer instance `app`.
2. All nested Typer apps (`db_app`, `portfolio_app`, `fundamentals_app`, `segments_app`, `futures_intraday_app`, `futures_depth_app`) should be registered onto `app` in `__init__.py` or within their respective submodules before `app` is exported.
3. `src/geoanalytics/cli/__main__.py` should be added to enable `python -m geoanalytics.cli`.

### 4.3 Proposed Submodule Breakdown (Target <600 lines each)

To resolve the 2,696-line God Object while adhering to the 600 LOC budget:

```
src/geoanalytics/cli/
├── __init__.py           # Root Typer app, sub-app registration, main entry point (<150 LOC)
├── __main__.py           # Execution entry point for `python -m geoanalytics.cli` (<10 LOC)
├── data.py               # Sources, ingest, news, digest, process, pipeline, backfill, prune, health (~450 LOC)
├── nlp.py                # Relink, rescore, reaspect, retemporal, reprocess, refactuality, renumeric, etc. (~500 LOC)
├── analytics.py          # Sentiment-index, event-study, attribution, graph, regime, pressure, asset, etc. (~550 LOC)
├── trading.py            # Alerts, backtest, walkforward, portfolio, fundamentals, segments, futures sub-apps (~500 LOC)
└── services.py           # Services: run-scheduler, run-futrader, run-bot, serve, db sub-app (~250 LOC)
```

---

## 5. Verification Plan

1. **Unit Test Suite**: Run `pytest tests/` to confirm 100% pass rate (1,243/1,243).
2. **CLI Commands Verification**: Execute:
   - `geo --help`
   - `geo serve --help`
   - `geo run-scheduler --help`
   - `geo run-futrader --help`
   - `geo futures-depth --help`
   - `geo db --help`
3. **Control Script Verification**: Run `./geo-ctl.sh status`.
4. **Line Count Budget Check**: Verify `wc -l src/geoanalytics/cli/*.py` ensures no single file exceeds 600 lines.
