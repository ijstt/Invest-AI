# Detailed Analysis: Geoanalytics Processing Refactoring

This report provides a detailed analysis of the monolithic `src/geoanalytics/processing.py` (HEAD / original version) and outlines the structures, line numbers, design, and modularization strategy to bring all files under the 600-line limit.

## Executive Summary
The original `src/geoanalytics/processing.py` file is a 1055-line monolith containing multiple redundant database batch-pagination loops and duplicate string constructions for `full_text`. A modular refactoring separates the file into a package `src/geoanalytics/processing/` containing `common.py` (generic iterator and helper functions), `reprocessing.py` (re-evaluation logic using the paginated query iterator), and `pipeline.py` (news, macro, and market stream ingestion).

---

## 1. File Length Evaluation
- **Original File Path**: `src/geoanalytics/processing.py` (deleted in the working branch but visible in HEAD)
- **Original Line Count**: 1055 lines.
- **Problem**: This exceeds the project constraint of keeping all files under 600 lines.
- **Recommended Split**:
  - `src/geoanalytics/processing/__init__.py`: Handles public API exports (re-exposing functions/classes for backward compatibility).
  - `src/geoanalytics/processing/common.py`: Shared utilities, `ProcessResult` dataclass, the generic pagination iterator, full text helper, and other private helpers (`_embed_batch`, `_extra_entity_rows`, etc.).
  - `src/geoanalytics/processing/pipeline.py`: Code for processing new records/documents (`_process_news`, `_process_market`, `_process_macro`, `process_pending`).
  - `src/geoanalytics/processing/reprocessing.py`: Re-evaluating existing database records (`relink_existing`, `rescore_existing`, `reaspect_existing`, `retemporal_existing`, `refactuality_existing`, `renumeric_existing`, `reforecast_existing`).

---

## 2. Offset-Batch-Pagination Loop Patterns
Six distinct methods in `original_processing.py` implement nearly identical patterns of offset-batch-pagination over database queries.

### Summary Table of Pagination Loops
| Method | Lines in Original | Target Entity | Batch Variable | DB Session Lifetime |
| :--- | :--- | :--- | :--- | :--- |
| `rescore_existing` | 621-651 | `Article` | `articles` | Single session per batch |
| `reaspect_existing` | 680-712 | `ArticleEntity` x `Article` x `Asset` | `rows` | Single session per batch |
| `retemporal_existing` | 744-770 | `Article` | `rows` | Single session per batch |
| `refactuality_existing` | 793-818 | `Article` | `rows` | Single session per batch |
| `renumeric_existing` | 840-870 | `Article` (id, title, text) | `rows` | Single session per batch |
| `reforecast_existing` | 892-930 | `Article` (broker channels) | `arts` | Single session per batch |

### Code Structure Analysis
All loops use a local `offset` variable, query using `.offset(offset).limit(take)`, update `offset += len(batch)`, and break if fewer elements than `take` are returned.

Example (from `rescore_existing` lines 621-629):
```python
    offset = 0
    while limit is None or result.articles < limit:
        take = batch_size if limit is None else min(batch_size, limit - result.articles)
        with session_scope() as session:
            articles = list(session.scalars(
                select(Article).order_by(Article.id).offset(offset).limit(take)
            ))
            if not articles:
                break
            # ... process articles ...
        offset += len(articles)
        if len(articles) < take:
            break
```

---

## 3. Duplicate `full_text` Constructions
There are exactly 7 occurrences of building a full text representation by concatenating the title and the text/body of the article, formatting them, and calling `.strip()`.

### Summary of `full_text` Duplicate Sites
| # | Method | Line | Exact Snippet |
| :--- | :--- | :--- | :--- |
| 1 | `relink_existing` | 465 | `full_text = f"{art.title}. {art.text or ''}".strip()` |
| 2 | `_rescore_article` | 545 | `full_text = f"{art.title}. {art.text or ''}".strip()` |
| 3 | `reaspect_existing` | 697 | `full_text = f"{title}. {body or ''}".strip()` |
| 4 | `retemporal_existing` | 756 | `full_text = f"{art.title}. {art.text or ''}".strip()` |
| 5 | `refactuality_existing` | 805 | `full_text = f"{art.title}. {art.text or ''}".strip()` |
| 6 | `renumeric_existing` | 853 | `facts = numeric.extract_numbers(f"{title}. {body or ''}".strip())` |
| 7 | `reforecast_existing` | 920 | `text = f"{art.title}. {art.text or ''}".strip()` |

*(Note: There is also `full_text = f"{title}. {body}".strip()` on line 226 of `_process_news`, which does not use the `or ''` fallback since the fields are pre-cleaned to strings).*

---

## 4. Proposed Refactoring Designs

### Generic Pagination Iterator
A helper function `paginate_query` can abstract the boilerplate database access, offset updating, limit enforcement, and session scope management:

```python
from collections.abc import Callable, Generator
from sqlalchemy.orm import Session
from geoanalytics.storage.db import session_scope

def paginate_query[T](
    fetch_fn: Callable[[Session, int, int], list[T]],
    batch_size: int,
    limit: int | None = None,
) -> Generator[tuple[Session, list[T]], None, None]:
    """Generically paginates query execution over database sessions."""
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

### Full Text Construction Helper
A simple helper function eliminates duplicate logic and provides consistent fallbacks for missing title/body components:

```python
def make_full_text(title: str | None, body: str | None) -> str:
    """Constructs clean full text from title and body/text components."""
    return f"{title or ''}. {body or ''}".strip()
```

---

## 5. Verification Status
Running unit and integration tests verifies that the split package preserves the expected public API.
Test command:
`pytest tests/test_processing.py`

All tests relating to `processing` functionality are fully passing, indicating that the modular package successfully replaces the original monolithic file without altering business logic or breaking the public contract.
