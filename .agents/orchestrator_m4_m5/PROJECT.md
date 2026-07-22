# Project: Invest-AI Modularization Refactoring (Milestones 4 & 5)

## Architecture
- `src/geoanalytics/api/web.py`: Lightweight FastAPI app assembler importing routers from `src/geoanalytics/api/routers/`.
- `src/geoanalytics/cli.py` & `src/geoanalytics/cli/`: Modular CLI with submodules (`cli/alerts.py`, `cli/nlp.py`, `cli/backtest.py`, `cli/market.py`, etc.) keeping `geo` CLI entry point intact.

## Code Layout
- `src/geoanalytics/api/web.py` (108 lines, app assembler)
- `src/geoanalytics/api/routers/` (8 sub-routers, all <260 lines)
- `src/geoanalytics/cli.py` (entry point dispatcher, target <600 lines)
- `src/geoanalytics/cli/` (modular submodules, target <600 lines each)

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 4 | Web API Modularization | Extract remaining endpoints from `web.py` into `routers/` | none | DONE |
| 5 | CLI Modularization | Split `cli.py` into `cli/` submodules | M4 | IN_PROGRESS |

## Interface Contracts
- Public APIs, endpoint paths, CLI flags, imports must remain identical.
- Unit tests in `tests/` must pass 100%.
- Raspberry Pi scripts in `deploy/pi/*` must remain intact.
