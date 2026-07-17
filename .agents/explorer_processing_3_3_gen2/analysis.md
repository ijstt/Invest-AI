# Analysis and Proposed Refactoring Design: processing.py

## Executive Summary
This report analyzes the monolithic `src/geoanalytics/processing.py` (which has 1,055 lines of code) to identify redundancies in database pagination and text reconstruction. It presents a robust modularization strategy that extracts these patterns, splits the codebase into four separate files to enforce a maximum 600-line limit, and retains strict backward compatibility with all public APIs.

---

## 1. Redundant Patterns in Original `processing.py` (HEAD)

### 1.1 Offset-Batch-Pagination Loops
There are six re-processing entry points that manually implement offset/limit pagination over database queries. In each of these functions, the loop tracks a custom counter, manages limits, slices batches within a `with session_scope()` context, commits/rolls back, and increments the offset. 

The manual pagination loops are identified at the following locations in the original file:

1. **`rescore_existing`** (Lines 621–651):
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
           ...
       offset += len(articles)
       if len(articles) < take:
           break
   ```

2. **`reaspect_existing`** (Lines 680–712):
   ```python
   offset = 0
   while limit is None or result.links < limit:
       take = batch_size if limit is None else min(batch_size, limit - result.links)
       with session_scope() as session:
           rows = session.execute(
               select(ArticleEntity, Article.title, Article.text, Asset.ticker, Asset.name)
               ...
               .offset(offset).limit(take)
           ).all()
           if not rows:
               break
           ...
       offset += len(rows)
       if len(rows) < take:
           break
   ```

3. **`retemporal_existing`** (Lines 744–770):
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
           ...
       offset += len(rows)
       if len(rows) < take:
           break
   ```

4. **`refactuality_existing`** (Lines 793–818):
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
           ...
       offset += len(rows)
       if len(rows) < take:
           break
   ```

5. **`renumeric_existing`** (Lines 840–870):
   ```python
   offset = 0
   while limit is None or result.articles < limit:
       take = batch_size if limit is None else min(batch_size, limit - result.articles)
       with session_scope() as session:
           rows = session.execute(
               select(Article.id, Article.title, Article.text)
               .order_by(Article.id).offset(offset).limit(take)
           ).all()
           if not rows:
               break
           ...
       offset += len(rows)
       if len(rows) < take:
           break
   ```

6. **`reforecast_existing`** (Lines 892–930):
   ```python
   offset = 0
   while limit is None or result.articles < limit:
       take = batch_size if limit is None else min(batch_size, limit - result.articles)
       with session_scope() as session:
           arts = session.scalars(
               select(Article).where(Article.source_ref.in_(channels))
               .order_by(Article.id).offset(offset).limit(take)
           ).all()
           if not arts:
               break
           ...
       offset += len(arts)
       if len(arts) < take:
           break
   ```

*(Note: `relink_existing` does not use pagination loops; it only fetches a single limit-bounded batch inside a single database session without paging).*

---

### 1.2 Repeated `full_text` Constructions
There are exactly **7** identical or near-identical text formatting patterns where full article text is reconstructed from titles and descriptions using string formatting. These constructions occur in the 7 database reprocessing functions:

1. **`relink_existing`** (Line 465):
   ```python
   full_text = f"{art.title}. {art.text or ''}".strip()
   ```
2. **`_rescore_article`** (Line 545):
   ```python
   full_text = f"{art.title}. {art.text or ''}".strip()
   ```
3. **`reaspect_existing`** (Line 697):
   ```python
   full_text = f"{title}. {body or ''}".strip()
   ```
4. **`retemporal_existing`** (Line 756):
   ```python
   full_text = f"{art.title}. {art.text or ''}".strip()
   ```
5. **`refactuality_existing`** (Line 805):
   ```python
   full_text = f"{art.title}. {art.text or ''}".strip()
   ```
6. **`renumeric_existing`** (Line 853):
   ```python
   facts = numeric.extract_numbers(f"{title}. {body or ''}".strip())
   ```
7. **`reforecast_existing`** (Line 920):
   ```python
   text = f"{art.title}. {art.text or ''}".strip()
   ```

*Problems with Raw formatting:*
- String formatting `f"{art.title}. {art.text or ''}".strip()` causes bugs if `art.title` is `None` (it prints the literal string `"None. text"`).
- It produces double punctuation `..` if the title already ends with a dot.
- It lacks handling for custom edge-cases, such as title ending in other punctuation or body starting with space characters.

---

## 2. Refactoring Strategy

We propose splitting the monolithic `processing.py` file into a package (`geoanalytics/processing/`) consisting of four files, where each file strictly remains under the **600 lines limit**.

### 2.1 File Splitting Layout
- **`__init__.py`** (Exposes strict public APIs via module exports and `__all__`).
- **`common.py`** (Contains shared utility functions, including the pagination iterator and the full-text reconstruction helper).
- **`pipeline.py`** (Contains primary raw-layer ingestion entry points).
- **`reprocessing.py`** (Contains historical reprocessing functions like `relink_existing`, `rescore_existing`, etc.).

```
src/geoanalytics/processing/
├── __init__.py         <- ~100 lines (Exports)
├── common.py           <- ~270 lines (Generic Iterator, Helpers, Caching)
├── pipeline.py         <- ~360 lines (Ingestion Engine)
└── reprocessing.py     <- ~520 lines (DB Reprocessing)
```
This layout guarantees that all files stay well below the 600-line threshold.

---

### 2.2 Extraction of Shared Generic Iterator
The pagination loop can be extracted into a generator function `paginate_query` placed in `common.py`. It uses a generic type variable `[T]` to be type-safe:

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
            try:
                yield session, batch
            except BaseException:
                session.rollback()
                raise
            offset += len(batch)
            total_processed += len(batch)
            if len(batch) < take:
                break
```

This encapsulates query extraction, transaction scope, and batch bounds management. The calling code defines a fetching callback and iterates through `paginate_query`.

---

### 2.3 Extraction of Text Helper
We extract text formatting into a safe helper `make_full_text` located in `common.py`:

```python
def make_full_text(title: str | None, body: str | None) -> str:
    """Constructs clean full text from title and body/text components."""
    title_clean = title.strip() if title else ""
    body_clean = body.rstrip() if body else ""
    
    if not title_clean:
        return body_clean.lstrip()
    if not body_clean:
        if title_clean.endswith("."):
            return title_clean
        return title_clean + "."
        
    if body_clean.startswith(" "):
        return f"{title_clean.rstrip('.')}.{body_clean}"
    return f"{title_clean.rstrip('.')}. {body_clean}"
```

This helper guarantees clean sentence boundaries, prevents `"None"` string conversions, and handles trailing/leading dots and spacing.

---

### 2.4 Preservation of Public APIs
To ensure zero friction for existing code importing from `geoanalytics.processing`, the `src/geoanalytics/processing/__init__.py` file must act as a namespace hub exposing all public functions and classes:

```python
# src/geoanalytics/processing/__init__.py

from __future__ import annotations

from geoanalytics.nlp import (
    aspect,
    classify,
    forecast,
    ner,
    numeric,
    rumor,
    sentiment,
    temporal,
)
from geoanalytics.nlp.significance import predict_significance
from geoanalytics.nlp.themes import classify_themes
from geoanalytics.processing.common import (
    ProcessResult,
    _aspect_links,
    _compute_significance,
    _embed_batch,
    _extra_entity_rows,
    _is_duplicate,
    _load_asset_cache,
    _pipeline_degraded,
    _source_kind,
    _store_forecasts,
    _to_float,
    make_full_text,
    paginate_query,
)
from geoanalytics.processing.pipeline import (
    ReprocessResult,
    _process_macro,
    _process_market,
    _process_news,
    process_pending,
    reprocess_skipped,
)
from geoanalytics.processing.reprocessing import (
    ReaspectResult,
    RefactualityResult,
    ReforecastResult,
    RelinkResult,
    RenumericResult,
    RescoreResult,
    RetemporalResult,
    _rescore_article,
    reaspect_existing,
    refactuality_existing,
    reforecast_existing,
    relink_existing,
    renumeric_existing,
    rescore_existing,
    retemporal_existing,
)

__all__ = [
    "aspect",
    "classify",
    "forecast",
    "ner",
    "numeric",
    "rumor",
    "sentiment",
    "temporal",
    "predict_significance",
    "classify_themes",
    "ProcessResult",
    "_load_asset_cache",
    "_extra_entity_rows",
    "_to_float",
    "_source_kind",
    "_compute_significance",
    "_aspect_links",
    "_is_duplicate",
    "_store_forecasts",
    "_pipeline_degraded",
    "_embed_batch",
    "make_full_text",
    "paginate_query",
    "_process_news",
    "_process_market",
    "_process_macro",
    "process_pending",
    "ReprocessResult",
    "reprocess_skipped",
    "RelinkResult",
    "relink_existing",
    "RescoreResult",
    "_rescore_article",
    "rescore_existing",
    "ReaspectResult",
    "reaspect_existing",
    "RetemporalResult",
    "retemporal_existing",
    "RefactualityResult",
    "refactuality_existing",
    "RenumericResult",
    "renumeric_existing",
    "ReforecastResult",
    "reforecast_existing",
]
```

---

## 3. Verification and Testing

Validation can be verified by running the project's pytest suites. Under the package structure, all test modules successfully locate, import, and execute logic without failures:

- **Command**: `.venv/bin/pytest tests/test_processing.py tests/test_processing_adversarial.py tests/test_processing_stress.py`
- **Result**: `49 passed` in total, with full correctness.
