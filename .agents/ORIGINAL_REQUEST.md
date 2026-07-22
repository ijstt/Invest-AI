# Original User Request

## 2026-07-22T16:00:30Z

You are tasked with completing the structural refactoring for the Invest-AI project located at `/home/ijstt/News`.

Working directory: /home/ijstt/News
Integrity mode: development

## Context
The previous agent team successfully completed Milestones 1, 2, and 3 (Processing and NLP modularization). All changes have been committed. The repository is currently in a clean, working state with all 1,228 unit tests passing (`pytest tests/`). However, the previous team was interrupted due to API rate limits while in the middle of Milestone 4. 

## Requirements

### R1. Complete Milestone 4: Web API Modularization
- The previous team began splitting the monolithic `src/geoanalytics/api/web.py` (which was >1K lines) into modular FastAPI routers in the `src/geoanalytics/api/routers/` directory (e.g., `dashboard.py`, `asset.py` have been created).
- Review the current state of `api/web.py` and `api/routers/`. Complete the extraction of endpoints so that `web.py` serves only as a lightweight app assembler that includes the routers.
- Ensure that `test_web.py` and all other tests continue to pass 100%.

### R2. Execute Milestone 5: CLI Modularization (God Object Resolution)
- Split the massive monolithic file `src/geoanalytics/cli.py` (~2.7K lines) into a `cli/` package with logical submodules (e.g., `cli/alerts.py`, `cli/nlp.py`, `cli/backtest.py`, `cli/market.py`, etc.).
- The main entry point should remain functional so that the CLI command (`geo`) continues to function identically to its pre-refactored state. You can verify this by running `./geo-ctl.sh status` or invoking a help command.

### R3. Strict Integrity & Raspberry Pi Integration
- The refactoring must be purely structural. You have full freedom to move code, create new directories, and rename private helpers, but the public APIs of the modules (as consumed by tests and the CLI) must remain intact.
- **Do not** simplify or delete code comments unless you are completely deleting the corresponding function/class.
- **Raspberry Pi Verification**: Ensure all deployment scripts (`deploy/pi/*`), database sync routines, and inter-device communication between the main laptop and Raspberry Pi remain fully intact and operational.

## Acceptance Criteria
- [ ] Running `source .venv/bin/activate && pytest tests/` exits with code 0 (100% pass rate).
- [ ] No single file in the project exceeds 600 lines of code after refactoring.
- [ ] The `geo` CLI command continues to function identically.
- [ ] Raspberry Pi deployment scripts and connectivity remain 100% functional.
