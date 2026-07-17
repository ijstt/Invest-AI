# Project: Invest-AI Monolith Refactoring

## Architecture
- `src/geoanalytics/`: Core package containing processing, NLP, API, and CLI.
- `src/geoanalytics/processing.py`: Handles data processing pipelines.
- `src/geoanalytics/api/web.py`: Web API entrypoint.
- `src/geoanalytics/nlp/`: NLP modules and loaders.
- `src/geoanalytics/cli.py`: Command line interface.

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|---|---|---|---|
| 1 | Baseline & Web Fixes | Investigate test suite, fix 4 failing tests in `test_web.py` | None | DONE |
| 2 | Processing Refactoring | Refactor `processing.py`, extract offset-batch iterator and `full_text` helper, keep files < 600 lines | M1 | DONE |
| 3 | NLP Refactoring & Tests | Create `nlp/_seqcls.py` loader, share logic with `sentiment.py`, fix `fundamentals.py` private imports, add unit tests for `ner.py`, `embeddings.py`, `llm.py`, and `_seqcls.py` | M1 | DONE |
| 4 | Web API modularization | Split `api/web.py` into modular routers, keep files < 600 lines | M1 | IN_PROGRESS |
| 5 | CLI modularization | Refactor `cli.py` into a `cli/` package, keep files < 600 lines, verify `./geo-ctl.sh` | M1 | PLANNED |
| 6 | Final verification | Run full test suite, verify no files exceed 600 lines, run adversarial coverage hardening | M2, M3, M4, M5 | PLANNED |

## Code Layout
- `src/geoanalytics/cli/`: New package for CLI submodules.
- `src/geoanalytics/api/routers/`: New package for modular web routers.
- `src/geoanalytics/nlp/`: Contains NLP submodules.
- `tests/`: Existing unit/integration tests.
- `.agents/`: Coordination and metadata files.
