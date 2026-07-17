# Scope: Web API Modularization

## Architecture
- `src/geoanalytics/api/web.py` (~1000 lines): Monolithic file containing web endpoints and routers.

## Objectives
- Split `api/web.py` into modular routers (e.g. grouped logically by entity or feature, such as articles, assets, alerts, etc.).
- Ensure all refactored/created files are strictly under 600 lines.
- Preserve all public API signatures and functionality.
- Verify that all unit/integration tests (specifically web API tests) pass 100%.

## Completion Criteria
- Pytest runs and passes 100%.
- Verified by Reviewer and Forensic Auditor (CLEAN verdict).
