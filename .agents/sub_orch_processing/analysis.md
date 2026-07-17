# Synthesis of Analysis & Refactoring Plan: `geoanalytics/processing.py`

## 1. Identified Duplication & Patterns

### 1.1 Offset-Batch-Pagination Loops
There are 6 history reprocessing functions that implement identical pagination logic:
1. `rescore_existing`
2. `reaspect_existing`
3. `retemporal_existing`
4. `refactuality_existing`
5. `renumeric_existing`
6. `reforecast_existing`

### 1.2 Repeated `full_text` Constructions
There are 8 occurrences where full text is built using title and text/body:
1. `_process_news`
2. `relink_existing`
3. `_rescore_article`
4. `reaspect_existing`
5. `retemporal_existing`
6. `refactuality_existing`
7. `reforecast_existing`
8. `renumeric_existing` (inline in `extract_numbers`)

---

## 2. Refactoring Design

We will convert the single file `src/geoanalytics/processing.py` into a package: `src/geoanalytics/processing/` containing:
- `__init__.py`: Package-level exports preserving all public and test-private APIs.
- `common.py`: Shared utilities, global caches, constants, `make_full_text`, and the generic iterator `paginate_query`.
- `pipeline.py`: Raw document ingestion pipeline logic (`process_pending`, `reprocess_skipped`, etc.).
- `reprocessing.py`: Batch database reprocessing logic using `paginate_query` and `make_full_text`.

### 2.1 Generic Iterator: `paginate_query`
In `common.py`:
```python
from collections.abc import Generator
from typing import Callable, TypeVar
from sqlalchemy.orm import Session
from geoanalytics.storage.db import session_scope

T = TypeVar("T")

def paginate_query(
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

### 2.2 Text Helper: `make_full_text`
In `common.py`:
```python
def make_full_text(title: str | None, body: str | None) -> str:
    """Constructs clean full text from title and body/text components."""
    return f"{title or ''}. {body or ''}".strip()
```

---

## 3. Package Structure & File Split

All submodules will remain under 600 lines:
1. `common.py` (~250 lines): Internal caches, helpers, `make_full_text`, and `paginate_query`.
2. `pipeline.py` (~350 lines): Pipeline process functions (`process_pending`, `reprocess_skipped`).
3. `reprocessing.py` (~450 lines): Reprocessing batch commands (`rescore_existing`, etc.).

Client modules and tests will import from `geoanalytics.processing` seamlessly via `__init__.py` exposing the original names.
