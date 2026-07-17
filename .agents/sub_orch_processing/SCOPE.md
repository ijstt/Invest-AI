# Scope: Processing Refactoring

## Architecture
- `src/geoanalytics/processing.py` (~1000 lines): Monolithic file containing data processing code.

## Objectives
- Extract repeated looping patterns (specifically, offset-batch-pagination loops) into a shared generic iterator.
- Move the 7 repeated `full_text` constructions into a single helper function.
- Split `processing.py` if necessary so that no single file (original or new submodules) exceeds 600 lines.
- Preserve all public API signatures and functionality, verifying that existing tests pass 100%.

## Completion Criteria
- Unit/integration tests pass 100% (specifically those calling processing functions).
- No file modified or created exceeds 600 lines of code.
- Reviewed and verified by Forensic Auditor (CLEAN verdict).
