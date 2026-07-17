# Scope: Baseline & Web Fixes

## Architecture
- `tests/test_web.py`: Contains web API tests, 4 of which are failing due to a recent template/context change (`unreal_pct`, `<datalist>`).

## Objectives
- Bring the test suite to 100% pass rate.
- Focus specifically on the 4 failing tests in `test_web.py`.

## Completion Criteria
- Running `pytest tests/` (specifically `tests/test_web.py`) passes 100%.
- Verified by a Reviewer and Forensic Auditor.
