# Handoff Report — explorer_processing_3_1

## 1. Observation
- The target file `/home/ijstt/News/src/geoanalytics/processing.py` does not exist:
  ```
  failed to read file: open /home/ijstt/News/src/geoanalytics/processing.py: no such file or directory
  ```
  Instead, the logic is split into a package under `/home/ijstt/News/src/geoanalytics/processing/`, where database-level reprocessing logic is defined in `reprocessing.py`.
- There are 7 instances of `make_full_text(...)` constructions in `reprocessing.py`:
  - **Line 73** (`relink_existing`): `full_text = make_full_text(art.title, art.text)`
  - **Line 148** (`_rescore_article`): `full_text = make_full_text(art.title, art.text)`
  - **Line 295** (`reaspect_existing`): `full_text = make_full_text(title, body)`
  - **Line 350** (`retemporal_existing`): `full_text = make_full_text(art.title, art.text)`
  - **Line 395** (`refactuality_existing`): `full_text = make_full_text(art.title, art.text)`
  - **Line 439** (`renumeric_existing`): `facts = numeric.extract_numbers(make_full_text(title, body))`
  - **Line 502** (`reforecast_existing`): `text = make_full_text(art.title, art.text)`
- There are 6 pagination loops calling `paginate_query(...)` in:
  - `rescore_existing` (Lines 231–251)
  - `reaspect_existing` (Lines 291–307)
  - `retemporal_existing` (Lines 346–361)
  - `refactuality_existing` (Lines 391–406)
  - `renumeric_existing` (Lines 435–454)
  - `reforecast_existing` (Lines 483–510)
- Running tests in the virtual environment passes completely:
  ```
  $ PYTHONPATH=src .venv/bin/pytest tests/test_processing*.py
  ============================== 49 passed in 5.07s ==============================
  ```

## 2. Logic Chain
- From the observed mapping of the package, the file `src/geoanalytics/processing/reprocessing.py` contains all the logic specified by the user.
- In 5 of the 7 text constructions, `make_full_text` accepts `art.title` and `art.text` directly from an `Article` object. In 2 cases, the SQL queries select discrete columns (`Article.title`, `Article.text`/`body`) to avoid full object retrieval. By changing these two queries to fetch `Article` objects, we can unify all 7 constructions to a single `article_full_text(art)` helper.
- The 6 offset-batch-pagination loops share identical structure: definition of local `fetch_fn`, execution of `paginate_query`, exception isolation with `try/except Exception`, custom error logging with ID mapping, and tracking error/processed counts.
- We can extract this pagination loop boilerplate into a generic, higher-order runner `process_paginated(...)` in `src/geoanalytics/processing/common.py`. This runner will support batch-level hook `prepare_batch_fn` (for N+1 query avoidance in `rescore_existing`) and nested transaction scopes (`session.begin_nested()`).
- Refactoring these loops will drastically reduce boilerplate and shrink `reprocessing.py` from 514 to ~320 lines, while `common.py` will stay at ~310 lines, ensuring both files remain well below the 600-line threshold.

## 3. Caveats
- Assumes that fetching the whole `Article` object in `reaspect_existing` and `renumeric_existing` rather than individual column variables is performant and acceptable. If strict column-level pruning is required, the `article_full_text` helper must be adapted to accept either an `Article` object or discrete inputs via python duck typing or generic argument signature.

## 4. Conclusion
- The refactoring is highly clean and beneficial. All 7 text constructions and 6 pagination loops can be successfully refactored. The public signatures and parameter structures will be preserved completely, maintaining 100% compatibility with the existing test suite.

## 5. Verification Method
- Independent command to run tests:
  ```bash
  PYTHONPATH=src .venv/bin/pytest tests/test_processing*.py
  ```
- File to inspect:
  `/home/ijstt/News/.agents/explorer_processing_3_1/analysis.md`
