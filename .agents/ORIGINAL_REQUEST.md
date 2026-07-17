# Original User Request

## Initial Request — 2026-07-16T15:39:36+03:00

Refactor the monolithic files in the Invest-AI project to improve maintainability, reduce code duplication, and establish a cleaner module hierarchy, without altering any business logic.

Working directory: /home/ijstt/News
Integrity mode: development

## Requirements

### R1. Resolve God Objects
Split massive monolithic files into cohesive, smaller modules inside new packages. Specifically:
- `src/geoanalytics/cli.py` (2.7K lines) should become a `cli/` package with logical submodules (e.g., `cli/alerts.py`, `cli/nlp.py`, `cli/backtest.py`).
- `src/geoanalytics/api/web.py` (1K lines) should be split into modular routers.
- `src/geoanalytics/processing.py` (1K lines) should extract repeated looping patterns (like the offset-batch-pagination loop) into a shared generic iterator, and move the 7 repeated `full_text` constructions into a single helper.

### R2. Eliminate NLP Duplication
- Create a shared model adapter loader in `nlp/_seqcls.py` to eliminate the copy-pasted `SeqClsAdapter` loading logic in `classify.py`, `significance.py`, `temporal.py`, and `aspect.py`.
- Refactor `sentiment.py` so that its custom `_RubertSentiment` class shares the `_is_full_model()` detection logic with `_seqcls.py`.

### R3. Strict API Preservation
The refactoring must be purely structural. You have full freedom to move code, create new directories, and rename private helpers, but the public API of the modules (as consumed by tests and the CLI) must remain intact.

### R4. Fix Private Imports
`nlp/fundamentals.py` currently imports private symbols (`_MULT`, `_to_float`) from `nlp/numeric.py`. Expose them properly as public API or extract them to a shared `_utils.py` module.

## Acceptance Criteria

### Verification
- [ ] Running `source .venv/bin/activate && pytest tests/` exits with code 0 (100% pass rate). Note: 4 tests in test_web.py are currently failing due to a recent template/context change (`unreal_pct`, `<datalist>`); you MUST fix them to reach 100%.
- [ ] No single file in the project exceeds 600 lines of code after refactoring.
- [ ] New unit tests are added for previously uncovered modules: `nlp/ner.py`, `nlp/embeddings.py`, `nlp/llm.py`, and `nlp/_seqcls.py`.
- [ ] The `geo` CLI command continues to function identically to its pre-refactored state (verify by running `./geo-ctl.sh status` or invoking a help command).
