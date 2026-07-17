# Invest-AI Refactoring Plan

## Milestones and Verification Strategy

### Milestone 1: Baseline & Web Fixes
- **Goal**: Bring the existing test suite to 100% success by fixing the 4 failing tests in `test_web.py`.
- **Subagents**: Explorer -> Worker -> Reviewer -> Challenger -> Auditor.
- **Verification**: Run `pytest tests/` and confirm 100% pass rate.

### Milestone 2: Processing Refactoring
- **Goal**: Refactor `processing.py` to extract repeated loops and `full_text` helper, ensuring all files are under 600 lines.
- **Subagents**: Explorer -> Worker -> Reviewer -> Challenger -> Auditor.
- **Verification**: Run unit/integration tests, verify `processing.py` and new helpers size < 600 lines.

### Milestone 3: NLP Refactoring & Tests
- **Goal**: Refactor NLP code loading duplication, fix private imports, write new tests for uncovered files.
- **Subagents**: Explorer -> Worker -> Reviewer -> Challenger -> Auditor.
- **Verification**: Run `pytest tests/` with new tests, confirm NLP requirements met.

### Milestone 4: Web API Modularization
- **Goal**: Split `api/web.py` into modular routers, files < 600 lines.
- **Subagents**: Explorer -> Worker -> Reviewer -> Challenger -> Auditor.
- **Verification**: Run API tests, verify new router files sizes.

### Milestone 5: CLI Modularization
- **Goal**: Split `cli.py` into `cli/` package, files < 600 lines.
- **Subagents**: Explorer -> Worker -> Reviewer -> Challenger -> Auditor.
- **Verification**: Verify CLI help and status command.

### Milestone 6: Final Verification
- **Goal**: Adversarial coverage hardening, check line limits across all files, full test execution.
- **Subagents**: Challenger -> Worker -> Reviewer -> Auditor.
- **Verification**: Auditor verdict CLEAN.
