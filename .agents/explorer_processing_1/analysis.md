# Detailed Codebase Analysis: Geoanalytics Data Processing Refactoring

This report provides a detailed analysis of the monolithic file `/home/ijstt/News/src/geoanalytics/processing.py` (saved from `HEAD` as `/home/ijstt/News/.agents/explorer_processing_1/original_processing.py`), focusing on locating duplicated code patterns, evaluating file sizes, and designing refactored generic components to improve maintainability and adherence to size limits.

---

## 1. Analysis of Offset-Batch-Pagination Loop Patterns

A core pattern of batch-processing exists across six migration/reprocessing functions. These loops are designed to load database records incrementally in batches (with a default `batch_size`), commit/close transactions on a per-batch basis to manage database locks and memory footprint, support processing limits, and break when there are no more records to process.

The six occurrences of this pattern in the monolithic `processing.py` are detailed below:

### Pattern 1: `rescore_existing`
* **Line Numbers**: 621 to 651
* **Database Query**: `select(Article).order_by(Article.id).offset(offset).limit(take)`
* **Structure**:
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
          # ... per-article rescoring with SAVEPOINT ...
      offset += len(articles)
      if len(articles) < take:
          break
  ```

### Pattern 2: `reaspect_existing`
* **Line Numbers**: 680 to 712
* **Database Query**: `select(ArticleEntity, Article.title, Article.text, Asset.ticker, Asset.name).join(Article...).join(Asset...).where(ArticleEntity.entity_type == EntityType.ASSET.value).order_by(ArticleEntity.id).offset(offset).limit(take)`
* **Structure**:
  ```python
  offset = 0
  while limit is None or result.links < limit:
      take = batch_size if limit is None else min(batch_size, limit - result.links)
      with session_scope() as session:
          rows = session.execute(...).all()
          if not rows:
              break
          # ... aspect analysis pair processing ...
      offset += len(rows)
      if len(rows) < take:
          break
  ```

### Pattern 3: `retemporal_existing`
* **Line Numbers**: 744 to 770
* **Database Query**: `select(Article).order_by(Article.id).offset(offset).limit(take)`
* **Structure**:
  ```python
  offset = 0
  while limit is None or result.articles < limit:
      take = batch_size if limit is None else min(batch_size, limit - result.articles)
      with session_scope() as session:
          rows = session.scalars(...).all()
          if not rows:
              break
          # ... temporal anchoring logic ...
      offset += len(rows)
      if len(rows) < take:
          break
  ```

### Pattern 4: `refactuality_existing`
* **Line Numbers**: 793 to 818
* **Database Query**: `select(Article).order_by(Article.id).offset(offset).limit(take)`
* **Structure**:
  ```python
  offset = 0
  while limit is None or result.articles < limit:
      take = batch_size if limit is None else min(batch_size, limit - result.articles)
      with session_scope() as session:
          rows = session.scalars(...).all()
          if not rows:
              break
          # ... factuality classification logic ...
      offset += len(rows)
      if len(rows) < take:
          break
  ```

### Pattern 5: `renumeric_existing`
* **Line Numbers**: 840 to 870
* **Database Query**: `select(Article.id, Article.title, Article.text).order_by(Article.id).offset(offset).limit(take)`
* **Structure**:
  ```python
  offset = 0
  while limit is None or result.articles < limit:
      take = batch_size if limit is None else min(batch_size, limit - result.articles)
      with session_scope() as session:
          rows = session.execute(...).all()
          if not rows:
              break
          # ... numeric fact extraction and insert ...
      offset += len(rows)
      if len(rows) < take:
          break
  ```

### Pattern 6: `reforecast_existing`
* **Line Numbers**: 892 to 930
* **Database Query**: `select(Article).where(Article.source_ref.in_(channels)).order_by(Article.id).offset(offset).limit(take)`
* **Structure**:
  ```python
  offset = 0
  while limit is None or result.articles < limit:
      take = batch_size if limit is None else min(batch_size, limit - result.articles)
      with session_scope() as session:
          arts = session.scalars(...).all()
          if not arts:
              break
          # ... broker forecast checking and storing ...
      offset += len(arts)
      if len(arts) < take:
          break
  ```

---

## 2. Analysis of the 7 Repeated `full_text` Constructions

There are 7 repeated constructions where the title and main text (or summary body) of an article are combined to form a single normalized text input for downstream NLP models. These constructions use the safe fallback form `f"{title_var}. {text_var or ''}".strip()`.

The 7 occurrences are documented below:

1. **`relink_existing` (Line 465)**:
   * Code: `full_text = f"{art.title}. {art.text or ''}".strip()`
2. **`_rescore_article` (Line 545)**:
   * Code: `full_text = f"{art.title}. {art.text or ''}".strip()`
3. **`reaspect_existing` (Line 697)**:
   * Code: `full_text = f"{title}. {body or ''}".strip()`
4. **`retemporal_existing` (Line 756)**:
   * Code: `full_text = f"{art.title}. {art.text or ''}".strip()`
5. **`refactuality_existing` (Line 805)**:
   * Code: `full_text = f"{art.title}. {art.text or ''}".strip()`
6. **`renumeric_existing` (Line 853)**:
   * Code: `facts = numeric.extract_numbers(f"{title}. {body or ''}".strip())`
7. **`reforecast_existing` (Line 920)**:
   * Code: `text = f"{art.title}. {art.text or ''}".strip()`

*Note: Line 226 (`f"{title}. {body}".strip()`) is excluded from this list because it is part of raw ingest validation (`_process_news`) where `body` is pre-cleaned and non-nullable, and does not use the `or ''` fallback.*

---

## 3. Evaluation of Line Count & Splitting Recommendations

### Current State
* **Monolithic File**: `src/geoanalytics/processing.py`
* **Total Line Count**: 1055 lines of code.
* **Problem**: Exceeds the target 600-line maintainability threshold.

### Splitting Proposal (Package Implementation)
To keep every file under 600 lines while maintaining 100% backward compatibility of the public API, we recommend replacing the monolithic `processing.py` with a package `src/geoanalytics/processing/` containing:

1. **`__init__.py`** (Re-exposes the public API)
   * *Line Count*: ~100 lines.
   * *Responsibility*: Re-exporting all public functions and models so that existing code imports (e.g. from `geoanalytics.processing import process_pending`) continue to function without modification.

2. **`common.py`** (Domain schemas and shared utilities)
   * *Line Count*: ~250 lines.
   * *Responsibility*: Housing `ProcessResult`, basic helper functions (like `_to_float`, `_source_kind`), aspect/derived entity resolvers (`_aspect_links`, `_extra_entity_rows`), database loading/embedding batchers (`_load_asset_cache`, `_embed_batch`), and the new pagination generator and full text constructor.

3. **`pipeline.py`** (Ingest workflow)
   * *Line Count*: ~350 lines.
   * *Responsibility*: Housing the core real-time processing flow: `process_pending`, `_process_news`, `_process_market`, `_process_macro`, and `reprocess_skipped`.

4. **`reprocessing.py`** (Migrations and historical reprocessing)
   * *Line Count*: ~520 lines.
   * *Responsibility*: Housing the batch migration scripts: `relink_existing`, `rescore_existing`, `_rescore_article`, `reaspect_existing`, `retemporal_existing`, `refactuality_existing`, `renumeric_existing`, and `reforecast_existing`.

---

## 4. Refactoring Design for Generic Components

To clean up code duplication, we designed two reusable helper components in `common.py`.

### Generic Pagination Iterator
The generic iterator `paginate_query` abstracts the offset-batch-pagination logic, transaction boundary management, and execution limits. It accepts a `fetch_fn` callback that executes queries inside each session batch.

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

#### Example Usage (`retemporal_existing`):
```python
def retemporal_existing(limit: int | None = None, batch_size: int = 500) -> RetemporalResult:
    result = RetemporalResult()
    if temporal._model() is None:
        log.warning("retemporal_no_model")
        return result

    def fetch_fn(session: Session, offset: int, take: int) -> list[Article]:
        return list(session.scalars(
            select(Article).order_by(Article.id).offset(offset).limit(take)
        ).all())

    for _session, rows in paginate_query(fetch_fn, batch_size, limit):
        for art in rows:
            result.articles += 1
            try:
                full_text = make_full_text(art.title, art.text)
                # temporal anchoring logic...
            except Exception as exc:
                ...
```

### Full-Text Construction Helper
The helper function `make_full_text` extracts title and body components, replacing 7 occurrences of duplicated string formatting.

```python
def make_full_text(title: str | None, body: str | None) -> str:
    """Constructs clean full text from title and body/text components."""
    return f"{title or ''}. {body or ''}".strip()
```
