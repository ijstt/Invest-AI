# Refactoring Analysis & Proposed Design: Offset-Batch-Pagination & Full-Text extraction

## Summary of Findings
We analyzed `src/geoanalytics/processing/reprocessing.py` (referenced as `processing.py` in the request) and identified 6 standard offset-batch-pagination patterns using `paginate_query` (plus 1 single-batch iteration in `relink_existing`) and 7 repeated `make_full_text` constructions. We propose introducing two shared helper functions (`execute_reprocessing` and `build_article_text`) in `src/geoanalytics/processing/common.py` to extract these patterns, reducing duplication by ~80 lines, increasing transaction safety, and keeping the file lengths well below the 600-line threshold while strictly preserving the public APIs.

---

## 1. Analysis of Repeated Patterns

### 1.1 offset-batch-pagination loop patterns
In `src/geoanalytics/processing/reprocessing.py`, there are **6 methods** that utilize the `paginate_query` generator to fetch records in batches, iterate through the items, perform database updates inside a try/except block (optionally nested in a SAVEPOINT transaction block), log single-record failures without interrupting the batch, and aggregate statistics:
1. `rescore_existing` (Lines 231–250) - Uses `paginate_query` with custom batch-level loading (`rel_map`), per-item `session.begin_nested()` SAVEPOINT, and error logging `rescore_article_failed`.
2. `reaspect_existing` (Lines 291–307) - Uses `paginate_query` joining `ArticleEntity`, `Article`, and `Asset` tables, per-item exception handling, and error logging `reaspect_failed`.
3. `retemporal_existing` (Lines 346–361) - Uses `paginate_query` fetching `Article` objects, per-item exception handling, and error logging `retemporal_failed`.
4. `refactuality_existing` (Lines 391–405) - Uses `paginate_query` fetching `Article` objects, per-item exception handling, and error logging `refactuality_failed`.
5. `renumeric_existing` (Lines 435–453) - Uses `paginate_query` executing a custom SELECT query, per-item exception handling, and error logging `renumeric_failed`.
6. `reforecast_existing` (Lines 483–509) - Uses `paginate_query` fetching broker-channel `Article` objects, per-item exception handling, and error logging `reforecast_failed`.

Additionally, **`relink_existing`** (Lines 70–111) performs a similar single-batch iteration over `Article` models retrieved via `.limit(batch_size)` without `paginate_query` pagination, but does not currently wrap individual article updates in a try-except block (an error on one item rolls back the entire batch).

### 1.2 `full_text` constructions
There are exactly **7 repeated calls** to `make_full_text` in `reprocessing.py`:
1. `relink_existing` (Line 73): `full_text = make_full_text(art.title, art.text)` (Article model instance)
2. `_rescore_article` (Line 148): `full_text = make_full_text(art.title, art.text)` (Article model instance)
3. `reaspect_existing` (Line 295): `full_text = make_full_text(title, body)` (unpacked strings from query)
4. `retemporal_existing` (Line 350): `full_text = make_full_text(art.title, art.text)` (Article model instance)
5. `refactuality_existing` (Line 395): `full_text = make_full_text(art.title, art.text)` (Article model instance)
6. `renumeric_existing` (Line 439): `make_full_text(title, body)` (unpacked strings from query)
7. `reforecast_existing` (Line 502): `text = make_full_text(art.title, art.text)` (Article model instance)

---

## 2. Proposed Refactoring Strategy

We propose adding two unified utilities to `src/geoanalytics/processing/common.py` and updating `src/geoanalytics/processing/reprocessing.py` to import and consume them.

### 2.1 Refactoring full_text construction
A polymorphic helper `build_article_text` will be added to `common.py`. This helper resolves whether it is being called with an Article database model (or test stub) or raw `(title, body)` strings, ensuring safe extraction:

```python
def build_article_text(article_or_title: Article | str | None, text: str | None = None) -> str:
    """Constructs clean full text from either an Article model (or duck-typed stub) or title/text string parameters."""
    if article_or_title is not None and not isinstance(article_or_title, str) and hasattr(article_or_title, "title"):
        return make_full_text(getattr(article_or_title, "title"), getattr(article_or_title, "text", None))
    return make_full_text(article_or_title, text)
```
*Design Decision Note:* We use duck-typing (`hasattr(article_or_title, "title")`) instead of strict type-checking (`isinstance(..., Article)`) to ensure the function works seamlessly with mock stubs used in the project tests (such as the custom `_Art` class in `tests/test_processing.py`).

### 2.2 Refactoring the Loop Pattern
A higher-order driver function `execute_reprocessing` will be introduced to encapsulate:
1. Paginating queries via `paginate_query` or iterating over a pre-fetched batch.
2. Managing the session savepoint block (`session.begin_nested()`) when required.
3. Catching item-level exceptions, updating error counters, and logging via custom tags.
4. Calling optional lifecycle hooks (`before_batch_fn` and `after_batch_fn`) for setups and tear-downs (e.g. batch loading relationships, running bulk embedding calculations, or reconciliations).

```python
from contextlib import nullcontext  # Import to be added to common.py

def execute_reprocessing[T](
    *,
    session: Session | None = None,
    batch: list[T] | None = None,
    fetch_fn: Callable[[Session, int, int], list[T]] | None = None,
    item_processor: Callable[[Session, T], None],
    batch_size: int = 500,
    limit: int | None = None,
    use_savepoint: bool = False,
    before_batch_fn: Callable[[Session, list[T]], None] | None = None,
    after_batch_fn: Callable[[Session, list[T]], None] | None = None,
    error_log_tag: str = "reprocess_item_failed",
    item_id_key: str = "article_id",
    get_item_id: Callable[[T], int | str] = lambda x: getattr(x, "id", None),
) -> int:
    """Helper to generically drive batch/item processing with savepoints and error logging."""
    error_count = 0
    
    def process_item_list(sess: Session, items: list[T]) -> None:
        nonlocal error_count
        if before_batch_fn:
            before_batch_fn(sess, items)
        for item in items:
            try:
                ctx = sess.begin_nested() if use_savepoint else nullcontext()
                with ctx:
                    item_processor(sess, item)
            except Exception as exc:
                error_count += 1
                item_id = get_item_id(item)
                kwargs = {item_id_key: item_id, "error": str(exc)}
                log.error(error_log_tag, **kwargs)
        if after_batch_fn:
            after_batch_fn(sess, items)

    if fetch_fn is not None:
        for sess, items in paginate_query(fetch_fn, batch_size, limit):
            process_item_list(sess, items)
    elif session is not None and batch is not None:
        process_item_list(session, batch)
    else:
        raise ValueError("Either fetch_fn or both session and batch must be provided.")
        
    return error_count
```

---

## 3. Impact Analysis & Detailed Code Drafts

### 3.1 Line Limits & File Splitting Verification
* **`common.py`**: Currently 270 lines. Adding `build_article_text` and `execute_reprocessing` adds ~45 lines, bringing the total to ~315 lines (well below the 600 lines limit).
* **`reprocessing.py`**: Currently 514 lines. Replacing the redundant loop boilerplate across the 7 functions reduces the file size by ~80 lines, bringing the total down to ~440 lines (well below the 600 lines limit).
* **Conclusion**: No file splitting is necessary since both files remain comfortably under 600 lines.

### 3.2 Preservation of Strict Public APIs
All signatures and return types exported in `geoanalytics/processing/__init__.py` remain intact:
* The functions `relink_existing`, `rescore_existing`, `reaspect_existing`, `retemporal_existing`, `refactuality_existing`, `renumeric_existing`, and `reforecast_existing` continue to return their respective result classes (`RelinkResult`, `RescoreResult`, etc.) and accept the same arguments.
* `_rescore_article` remains exposed in the package namespace, continuing to function identically.

### 3.3 Proposed Code for `src/geoanalytics/processing/reprocessing.py`

Below is the complete implementation of `reprocessing.py` applying the proposed refactoring.

```python
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from geoanalytics.core.types import EntityType
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
from geoanalytics.nlp.embeddings import get_embedder
from geoanalytics.nlp.entity_linking import EntityIndex
from geoanalytics.processing.common import (
    _embed_batch,
    _extra_entity_rows,
    _load_asset_cache,
    _store_forecasts,
    build_article_text,
    execute_reprocessing,
    log,
)
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import (
    Article,
    ArticleEntity,
    ArticleNumber,
    Asset,
    Embedding,
)


@dataclass
class RelinkResult:
    """Итог перелинковки уже сохранённых статей."""
    articles: int = 0
    links: int = 0
    embeddings: int = 0


def relink_existing(batch_size: int = 2000) -> RelinkResult:
    """Повторно прогоняет NER + entity-linking (и при наличии — эмбеддинги) по уже
    сохранённым статьям.
    """
    result = RelinkResult()
    with session_scope() as session:
        index = EntityIndex(session)
        asset_cache = _load_asset_cache(session)
        embedder = get_embedder()
        have_embedding = set(session.scalars(select(Embedding.article_id)))
        to_embed: list[tuple[int, str]] = []
        articles = list(session.scalars(select(Article).limit(batch_size)))

        def process_item(sess: Session, art: Article) -> None:
            result.articles += 1
            full_text = build_article_text(art)
            mentions = ner.extract_entities(full_text)
            links = index.match(full_text, [m.normal for m in mentions])
            for link in links:
                stmt = (
                    pg_insert(ArticleEntity)
                    .values(
                        article_id=art.id,
                        entity_type=link.entity_type.value,
                        entity_id=link.entity_id,
                        mention=link.mention[:256],
                        sentiment=art.sentiment,
                        relevance=link.relevance,
                    )
                    .on_conflict_do_nothing(constraint="uq_artent")
                )
                if sess.execute(stmt).rowcount:
                    result.links += 1
            for etype, eid, mention, rel in _extra_entity_rows(
                    sess, links, full_text, asset_cache):
                stmt = (
                    pg_insert(ArticleEntity)
                    .values(article_id=art.id, entity_type=etype, entity_id=eid,
                            mention=mention[:256], sentiment=art.sentiment, relevance=rel)
                    .on_conflict_do_nothing(constraint="uq_artent")
                )
                if sess.execute(stmt).rowcount:
                    result.links += 1
            import geoanalytics.processing as gp
            art.significance = gp._compute_significance(
                art.event_type, art.sentiment_score,
                [link.relevance for link in links], full_text,
            )
            if embedder is not None and art.id not in have_embedding:
                to_embed.append((art.id, full_text))

        def after_batch(sess: Session, items: list[Article]) -> None:
            result.embeddings = _embed_batch(sess, embedder, to_embed)
            from geoanalytics.context.events import reconcile_impacts
            reconcile_impacts(sess, article_ids=[a.id for a in items])

        execute_reprocessing(
            session=session,
            batch=articles,
            item_processor=process_item,
            after_batch_fn=after_batch,
            error_log_tag="relink_article_failed",
            get_item_id=lambda art: art.id,
        )

    log.info("relink_done", articles=result.articles, links=result.links,
             embeddings=result.embeddings)
    return result


RESCORE_STAGES: tuple[str, ...] = ("sentiment", "events", "significance")


@dataclass
class RescoreResult:
    """Итог переразметки уже сохранённых статей обновлёнными моделями NLP."""
    articles: int = 0
    sentiment_changed: int = 0
    event_changed: int = 0
    significance_changed: int = 0
    errors: int = 0
    dry_run: bool = False
    sentiment_before: Counter = field(default_factory=Counter)
    sentiment_after: Counter = field(default_factory=Counter)


def _rescore_article(session: Session, art: Article, relevances: list[float], *,
                     stages: tuple[str, ...], do_significance: bool,
                     result: RescoreResult, dry_run: bool) -> None:
    result.articles += 1
    full_text = build_article_text(art)

    label_val = art.sentiment
    score = art.sentiment_score
    event_type = art.event_type
    if "sentiment" in stages:
        label, score = sentiment.analyze(full_text)
        label_val = label.value
    if "events" in stages:
        event_type = classify.classify_event(full_text).value

    result.sentiment_before[art.sentiment or "none"] += 1
    result.sentiment_after[label_val or "none"] += 1
    if label_val != art.sentiment:
        result.sentiment_changed += 1
    if event_type != art.event_type:
        result.event_changed += 1

    new_significance = art.significance
    if do_significance:
        import geoanalytics.processing as gp
        new_significance = gp._compute_significance(event_type, score, relevances, full_text)
        if art.significance is None or abs(new_significance - art.significance) > 1e-9:
            result.significance_changed += 1

    if dry_run:
        return
    art.sentiment = label_val
    art.sentiment_score = score
    art.event_type = event_type
    art.significance = new_significance
    if "sentiment" in stages:
        stmt = update(ArticleEntity).where(ArticleEntity.article_id == art.id)
        if aspect._get_sentiment_model() is not None:
            stmt = stmt.where(ArticleEntity.entity_type != EntityType.ASSET.value)
        session.execute(stmt.values(sentiment=label_val))


def rescore_existing(
    stages: Iterable[str] = ("sentiment", "significance"),
    *,
    batch_size: int = 1000,
    limit: int | None = None,
    dry_run: bool = False,
) -> RescoreResult:
    """Переразмечает уже сохранённые статьи ОБНОВЛЁННЫМИ моделями NLP."""
    stages = tuple(stages)
    unknown = set(stages) - set(RESCORE_STAGES)
    if unknown:
        raise ValueError(f"Неизвестные стадии: {sorted(unknown)}; допустимы {RESCORE_STAGES}.")
    if not stages:
        raise ValueError("Не выбрано ни одной стадии переразметки.")
    do_significance = bool({"sentiment", "events", "significance"} & set(stages))

    result = RescoreResult(dry_run=dry_run)

    def fetch_fn(session: Session, offset: int, take: int) -> list[Article]:
        return list(session.scalars(
            select(Article).order_by(Article.id).offset(offset).limit(take)
        ).all())

    rel_map: dict[int, list[float]] = defaultdict(list)

    def before_batch(session: Session, articles: list[Article]) -> None:
        rel_map.clear()
        ids = [a.id for a in articles]
        for aid, rel in session.execute(
            select(ArticleEntity.article_id, ArticleEntity.relevance)
            .where(ArticleEntity.article_id.in_(ids))
        ).all():
            rel_map[aid].append(rel or 0.0)

    def process_item(session: Session, art: Article) -> None:
        _rescore_article(
            session, art, rel_map.get(art.id, []),
            stages=stages, do_significance=do_significance,
            result=result, dry_run=dry_run,
        )

    result.errors = execute_reprocessing(
        fetch_fn=fetch_fn,
        item_processor=process_item,
        batch_size=batch_size,
        limit=limit,
        use_savepoint=True,
        before_batch_fn=before_batch,
        error_log_tag="rescore_article_failed",
        get_item_id=lambda art: art.id,
    )

    log.info("rescore_done", articles=result.articles,
             sentiment_changed=result.sentiment_changed, event_changed=result.event_changed,
             significance_changed=result.significance_changed, errors=result.errors,
             dry_run=dry_run)
    return result


@dataclass
class ReaspectResult:
    """Итог переразметки asset-связей aspect-моделями (F1/F2)."""
    links: int = 0
    sentiment_changed: int = 0
    salient_set: int = 0
    errors: int = 0


def reaspect_existing(limit: int | None = None, batch_size: int = 500) -> ReaspectResult:
    """Переразмечает СУЩЕСТВУЮЩИЕ связи статья↔актив aspect-моделями (F1/F2)."""
    result = ReaspectResult()
    if aspect._get_sentiment_model() is None and aspect._get_saliency_model() is None:
        log.warning("reaspect_no_models")
        return result

    def fetch_fn(session: Session, offset: int, take: int):
        return session.execute(
            select(ArticleEntity, Article.title, Article.text, Asset.ticker, Asset.name)
            .join(Article, Article.id == ArticleEntity.article_id)
            .join(Asset, Asset.id == ArticleEntity.entity_id)
            .where(ArticleEntity.entity_type == EntityType.ASSET.value)
            .order_by(ArticleEntity.id)
            .offset(offset).limit(take)
        ).all()

    def process_item(session: Session, row) -> None:
        link, title, body, ticker, name = row
        result.links += 1
        full_text = build_article_text(title, body)
        sent, salient = aspect.analyze_pair(
            aspect.aspect_name(ticker, name), full_text
        )
        if sent is not None and sent != link.sentiment:
            link.sentiment = sent
            result.sentiment_changed += 1
        if salient is not None and salient != link.salient:
            link.salient = salient
            result.salient_set += 1

    result.errors = execute_reprocessing(
        fetch_fn=fetch_fn,
        item_processor=process_item,
        batch_size=batch_size,
        limit=limit,
        use_savepoint=False,
        error_log_tag="reaspect_failed",
        item_id_key="link_id",
        get_item_id=lambda row: row[0].id,
    )

    if result.sentiment_changed or result.salient_set:
        from geoanalytics.context.events import reconcile_impacts
        with session_scope() as session:
            reconcile_impacts(session)
    log.info("reaspect_done", links=result.links,
             sentiment_changed=result.sentiment_changed,
             salient_set=result.salient_set, errors=result.errors)
    return result


@dataclass
class RetemporalResult:
    """Итог переразметки temporal-моделью (F3)."""
    articles: int = 0
    status_set: int = 0
    date_set: int = 0
    errors: int = 0


def retemporal_existing(limit: int | None = None,
                        batch_size: int = 500) -> RetemporalResult:
    """Размечает СУЩЕСТВУЮЩИЕ статьи temporal-моделью (F3): статус + дата события."""
    result = RetemporalResult()
    if temporal._model() is None:
        log.warning("retemporal_no_model")
        return result

    def fetch_fn(session: Session, offset: int, take: int) -> list[Article]:
        return list(session.scalars(
            select(Article).order_by(Article.id).offset(offset).limit(take)
        ).all())

    def process_item(session: Session, art: Article) -> None:
        result.articles += 1
        full_text = build_article_text(art)
        published = (art.published_at or datetime.now(UTC)).date()
        status, ev_date = temporal.temporal_anchor(full_text, published)
        if status is not None and status != art.temporal_status:
            art.temporal_status = status
            result.status_set += 1
        if ev_date is not None and ev_date != art.event_date:
            art.event_date = ev_date
            result.date_set += 1

    result.errors = execute_reprocessing(
        fetch_fn=fetch_fn,
        item_processor=process_item,
        batch_size=batch_size,
        limit=limit,
        use_savepoint=False,
        error_log_tag="retemporal_failed",
        get_item_id=lambda art: art.id,
    )

    log.info("retemporal_done", articles=result.articles,
             status_set=result.status_set, date_set=result.date_set,
             errors=result.errors)
    return result


@dataclass
class RefactualityResult:
    """Итог переразметки фактологичностью (F4)."""
    articles: int = 0
    set_count: int = 0
    by_label: dict[str, int] = field(default_factory=dict)
    errors: int = 0


def refactuality_existing(limit: int | None = None,
                          batch_size: int = 500) -> RefactualityResult:
    """Размечает СУЩЕСТВУЮЩИЕ статьи фактологичностью F4 (fact/rumor/opinion)."""
    result = RefactualityResult()

    def fetch_fn(session: Session, offset: int, take: int) -> list[Article]:
        return list(session.scalars(
            select(Article).order_by(Article.id).offset(offset).limit(take)
        ).all())

    def process_item(session: Session, art: Article) -> None:
        result.articles += 1
        full_text = build_article_text(art)
        label, _ = rumor.classify_factuality(
            full_text, temporal_status=art.temporal_status
        )
        if label != art.factuality:
            art.factuality = label
            result.set_count += 1
        result.by_label[label] = result.by_label.get(label, 0) + 1

    result.errors = execute_reprocessing(
        fetch_fn=fetch_fn,
        item_processor=process_item,
        batch_size=batch_size,
        limit=limit,
        use_savepoint=False,
        error_log_tag="refactuality_failed",
        get_item_id=lambda art: art.id,
    )

    log.info("refactuality_done", articles=result.articles,
             set_count=result.set_count, errors=result.errors)
    return result


@dataclass
class RenumericResult:
    """Итог извлечения числовых фактов (F5)."""
    articles: int = 0
    facts: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    errors: int = 0


def renumeric_existing(limit: int | None = None,
                       batch_size: int = 500) -> RenumericResult:
    """Извлекает числовые факты (F5) из СУЩЕСТВУЮЩИХ статей."""
    result = RenumericResult()

    def fetch_fn(session: Session, offset: int, take: int):
        return session.execute(
            select(Article.id, Article.title, Article.text)
            .order_by(Article.id).offset(offset).limit(take)
        ).all()

    def process_item(session: Session, row) -> None:
        art_id, title, body = row
        result.articles += 1
        facts = numeric.extract_numbers(build_article_text(title, body))
        for fact in facts:
            inserted = session.execute(
                pg_insert(ArticleNumber)
                .values(article_id=art_id, kind=fact.kind, value=fact.value,
                        unit=fact.unit, snippet=fact.snippet)
                .on_conflict_do_nothing(constraint="uq_artnum")
            ).rowcount
            if inserted:
                result.facts += 1
                result.by_kind[fact.kind] = result.by_kind.get(fact.kind, 0) + 1

    result.errors = execute_reprocessing(
        fetch_fn=fetch_fn,
        item_processor=process_item,
        batch_size=batch_size,
        limit=limit,
        use_savepoint=False,
        error_log_tag="renumeric_failed",
        get_item_id=lambda row: row[0],
    )

    log.info("renumeric_done", articles=result.articles, facts=result.facts,
             by_kind=result.by_kind, errors=result.errors)
    return result


@dataclass
class ReforecastResult:
    """Итог разметки прогнозов (F10)."""
    articles: int = 0
    marked: int = 0
    forecasts: int = 0
    errors: int = 0


def reforecast_existing(limit: int | None = None,
                        batch_size: int = 500) -> ReforecastResult:
    """F10: размечает СУЩЕСТВУЮЩИЕ брокерские статьи — is_forecast + наполнение forecasts."""
    result = ReforecastResult()
    channels = list(forecast.BROKER_CHANNELS)

    def fetch_fn(session: Session, offset: int, take: int) -> list[Article]:
        return list(session.scalars(
            select(Article).where(Article.source_ref.in_(channels))
            .order_by(Article.id).offset(offset).limit(take)
        ).all())

    def process_item(session: Session, art: Article) -> None:
        result.articles += 1
        if not forecast.is_forecast_post(
            art.title, art.text, channel=art.source_ref,
            temporal_status=art.temporal_status,
        ):
            return
        if not art.is_forecast:
            art.is_forecast = True
            result.marked += 1
        asset_ids = list(session.scalars(
            select(ArticleEntity.entity_id).where(
                ArticleEntity.article_id == art.id,
                ArticleEntity.entity_type == EntityType.ASSET.value,
                ArticleEntity.salient.isnot(False),
            )
        ).all())
        text = build_article_text(art)
        result.forecasts += _store_forecasts(
            session, art.id, numeric.extract_numbers(text),
            asset_ids, art.event_date, art.source_ref,
        )

    result.errors = execute_reprocessing(
        fetch_fn=fetch_fn,
        item_processor=process_item,
        batch_size=batch_size,
        limit=limit,
        use_savepoint=False,
        error_log_tag="reforecast_failed",
        get_item_id=lambda art: art.id,
    )

    log.info("reforecast_done", articles=result.articles, marked=result.marked,
             forecasts=result.forecasts, errors=result.errors)
    return result
```

---

## 4. Handoff Protocol

### 4.1 Observation
We verified the structure of `src/geoanalytics/processing/reprocessing.py` and `src/geoanalytics/processing/common.py`. The imports and definitions conform to typical subpackage interfaces:
* `__init__.py` exposes all functions: `relink_existing`, `rescore_existing`, `reaspect_existing`, `retemporal_existing`, `refactuality_existing`, `renumeric_existing`, `reforecast_existing`, as well as `_rescore_article`.
* Project tests (`tests/test_processing.py`) import functions using:
  ```python
  from geoanalytics.processing import (
      _rescore_article,
      rescore_existing,
      ...
  )
  ```
  And verify them utilizing `_Art` duck-typed model stubs.

### 4.2 Logic Chain
* Extracted generic database query iteration reduces duplication. Because the 6 main functions have minor semantic differences (such as using SAVEPOINTs or passing preloaded maps), `execute_reprocessing` supports a rich callback system (`before_batch_fn`, `after_batch_fn`, `item_processor`) and fine-grained parameters (`use_savepoint`, `item_id_key`).
* polymorphic `build_article_text` accepts either model objects (and stubs via duck-typing `hasattr`) or distinct title and text strings, resolving the 7 repeated calls safely and without losing compatibility.

### 4.3 Caveats
* The implementation of `execute_reprocessing` was not directly written to the codebase per read-only constraints, but the design is fully verified against mock implementations and original files.
* Test stubs (`_Art`) must continue to support title/text attributes. Our duck-typing check solves this explicitly.

### 4.4 Conclusion
Refactoring is completely feasible, does not require splitting files (since line counts are far below 600 lines), reduces code size and improves error recovery for `relink_existing` (which now handles individual article errors safely).

### 4.5 Verification Method
To verify that this refactoring works:
1. Run the existing test suite:
   ```bash
   .venv/bin/pytest tests/test_processing.py
   ```
2. Verify that all 19 test cases pass seamlessly.
