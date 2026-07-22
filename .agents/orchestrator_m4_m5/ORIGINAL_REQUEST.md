# Original User Request

## 2026-07-22T19:00:48Z

Complete the structural refactoring for Invest-AI:
- Milestone 4: Web API Modularization (extract remaining endpoints from src/geoanalytics/api/web.py into src/geoanalytics/api/routers/, web.py serves only as lightweight app assembler).
- Milestone 5: CLI Modularization (split src/geoanalytics/cli.py ~2.7K lines into src/geoanalytics/cli/ submodules, geo CLI entry point intact).

Strict constraints:
- Purely structural refactoring: keep public APIs intact, retain code comments.
- 100% unit tests passing (`pytest tests/`).
- No file > 600 lines.
- Raspberry Pi deployment scripts and connectivity intact.
