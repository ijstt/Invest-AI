# Analysis: Processing Refactoring

This report contains the read-only investigation and design analysis for refactoring the database processing and NLP scoring pipeline of the Invest-AI project. The main goal is to refactor `src/geoanalytics/processing.py` to eliminate code duplication and split the monolith, keeping all files strictly under 600 lines of code.

---

## 1. Scope & Context
* **Original File**: `src/geoanalytics/processing.py` (1055 lines)
* **Goal**:
  * Extract 6 repeated offset-batch-pagination loop patterns into a shared generic iterator.
  * Extract 7 (or 8) repeated `full_text` constructions into a shared helper function.
  * Split the file so that no single file (original, package-level, or new submodules) exceeds 600 lines of code.
  * Maintain the identical public API and ensure all tests pass (100% success rate).

---

## 2. Offset-Batch-Pagination Loop Patterns

The original file contains **6 functions** implementing bulk data reprocessing or extraction. Each of these functions has an offset-batch-pagination loop with identical control logic.

### Identified Loop Locations
The pagination loop pattern is found in the following locations in the original `processing.py`:

| # | Function | Original Line Range | Description |
|---|---|---|---|
| 1 | `rescore_existing` | 621 - 651 | Recalculate NLP labels (sentiment, events, significance) for existing articles. |
| 2 | `reaspect_existing` | 680 - 712 | Re-evaluate aspect-level sentiment and saliency for article-asset pairs. |
| 3 | `retemporal_existing` | 744 - 770 | Re-anchor published times and event dates using updated temporal models. |
| 4 | `refactuality_existing` | 793 - 818 | Classify existing articles for factuality (fact vs. rumor/opinion). |
| 5 | `renumeric_existing` | 840 - 870 | Re-extract numeric values (target prices, dividends) from article text. |
| 6 | `reforecast_existing` | 892 - 930 | Re-process and extract target forecast values from broker channel posts. |

### Original Loop Structure
All 6 loops follow this control flow structure:
```python
offset = 0
while limit is None or <count_variable> < limit:
    take = batch_size if limit is None else min(batch_size, limit - <count_variable>)
    with session_scope() as session:
        # 1. Query the database using offset(offset).limit(take)
        # 2. Break if no rows are returned
        # 3. Process the returned rows within session scope
    offset += len(rows)
    if len(rows) < take:
        break
```
This pattern exposes several drawbacks:
* **Duplication**: Same index math (`take` calculation, `offset` updating, and break conditions) copied 6 times.
* **Session Scope Management**: Boilerplate `with session_scope() as session` inside the loop.
* **Risk of Infinite Loops**: Errors in offset computation or incorrect counts can lead to infinite loops.

---

## 3. Repeated `full_text` Constructions

A composite text field (`full_text`) is built by joining the article title and body text, stripping leading/trailing whitespace. This is used as the input text for NLP tasks (NER, sentiment analysis, event classification, etc.).

There are **8 instances** of this construction in the original code, 7 of which explicitly fallback to an empty string (`or ''`) for the body/text argument:

| # | Function | Original Line Number | Exact Code Content |
|---|---|---|---|
| 1 | `_process_news` | 226 | `full_text = f"{title}. {body}".strip()` |
| 2 | `relink_existing` | 465 | `full_text = f"{art.title}. {art.text or ''}".strip()` |
| 3 | `_rescore_article` | 545 | `full_text = f"{art.title}. {art.text or ''}".strip()` |
| 4 | `reaspect_existing` | 697 | `full_text = f"{title}. {body or ''}".strip()` |
| 5 | `retemporal_existing` | 756 | `full_text = f"{art.title}. {art.text or ''}".strip()` |
| 6 | `refactuality_existing` | 805 | `full_text = f"{art.title}. {art.text or ''}".strip()` |
| 7 | `renumeric_existing` | 853 | `f"{title}. {body or ''}".strip()` *(Passed directly as argument)* |
| 8 | `reforecast_existing` | 920 | `text = f"{art.title}. {art.text or ''}".strip()` |

*Note*: The first construction in `_process_news` (line 226) operates on cleaned strings (`title` and `body`) where the fallback is not required, while the remaining 7 constructions handle fields that can potentially be `None` from database or raw payloads.

---

## 4. File Length & Splitting Recommendations

The original `processing.py` file is **1055 lines long**, which significantly exceeds the project's target limit of **600 lines per file**. 

To resolve this God Object, we recommend splitting the file into a package: `src/geoanalytics/processing/` containing four modules. This keeps each module tightly scoped and well under 600 lines of code:

1. **`src/geoanalytics/processing/__init__.py`** (~100 lines)
   * **Purpose**: Package entry point. Exposes all public signatures from the submodules to maintain the exact public API, guaranteeing backward compatibility.
2. **`src/geoanalytics/processing/common.py`** (~250 lines)
   * **Purpose**: Holds common helpers, models (`ProcessResult`), cache loaders (`_load_asset_cache`), database operations (`_store_forecasts`, `_extra_entity_rows`), and the new generic pagination/text helpers.
3. **`src/geoanalytics/processing/pipeline.py`** (~350 lines)
   * **Purpose**: Implements the main processing queue and ingestion loop handlers (`process_pending`, `reprocess_skipped`, `_process_news`, `_process_market`, `_process_macro`).
4. **`src/geoanalytics/processing/reprocessing.py`** (~515 lines)
   * **Purpose**: Contains the bulk historical reprocessing commands (`relink_existing`, `rescore_existing`, `reaspect_existing`, `retemporal_existing`, `refactuality_existing`, `renumeric_existing`, `reforecast_existing`).

---

## 5. Design of Generic Pagination Iterator

To centralize the batching logic and simplify the bulk functions, we design a generic generator function called `paginate_query`.

### Code Implementation
```python
from collections.abc import Callable, Generator
from sqlalchemy.orm import Session
from geoanalytics.storage.db import session_scope

def paginate_query[T](
    fetch_fn: Callable[[Session, int, int], list[T]],
    batch_size: int,
    limit: int | None = None,
) -> Generator[tuple[Session, list[T]], None, None]:
    """Generically paginates query execution over database sessions.

    Yields a tuple of (session, batch_items) for each page.
    """
    offset = 0
    total_processed = 0
    while limit is None or total_processed < limit:
        take = batch_size if limit is None else min(batch_size, limit - total_processed)
        with session_scope() as session:
            batch = fetch_fn(session, offset, take)
            if not batch:
                break
            yield session, batch
            offset += len(batch)
            total_processed += len(batch)
            if len(batch) < take:
                break
```

### Usage Example (in `retemporal_existing`)
Using this iterator, the original code:
```python
    offset = 0
    while limit is None or result.articles < limit:
        take = batch_size if limit is None else min(batch_size, limit - result.articles)
        with session_scope() as session:
            rows = session.scalars(
                select(Article).order_by(Article.id).offset(offset).limit(take)
            ).all()
            if not rows:
                break
            for art in rows:
                result.articles += 1
                ...
        offset += len(rows)
        if len(rows) < take:
            break
```
Refactors cleanly to:
```python
    def fetch_fn(session: Session, offset: int, take: int) -> list[Article]:
        return list(session.scalars(
            select(Article).order_by(Article.id).offset(offset).limit(take)
        ).all())

    for _session, rows in paginate_query(fetch_fn, batch_size, limit):
        for art in rows:
            result.articles += 1
            ...
```

---

## 6. Design of `full_text` Helper Function

We design a shared helper `make_full_text` that ensures consistent and safe combination of title and text strings, handling `None` values gracefully.

### Code Implementation
```python
def make_full_text(title: str | None, body: str | None) -> str:
    """Constructs clean full text from title and body/text components."""
    return f"{title or ''}. {body or ''}".strip()
```

### Usage Example
Replacing repeated occurrences:
* **Before**: `full_text = f"{art.title}. {art.text or ''}".strip()`
* **After**: `full_text = make_full_text(art.title, art.text)`

---

## 7. Verification Method

To verify the design is functionally identical to the original monolith:
1. Ensure all files under `src/geoanalytics/processing/` compile properly and resolve imports.
2. Run the test suite:
   ```bash
   source .venv/bin/activate && pytest tests/
   ```
3. Run specifically the processing tests:
   ```bash
   source .venv/bin/activate && pytest tests/test_processing.py
   ```
4. Confirm all tests pass 100%.
