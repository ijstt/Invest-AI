# Handoff Report — Processing Verification Challenger

## 1. Observation
I observed the following:
- Running `.venv/bin/pytest tests/test_processing.py` succeeded: `19 passed in 5.63s`.
- Running `.venv/bin/pytest tests/test_processing_adversarial.py` succeeded: `7 passed in 0.62s`.
- Running `.venv/bin/pytest tests/test_processing_stress.py` succeeded: `23 passed in 0.71s`.
- In `tests/test_processing_adversarial.py` (lines 225-245), the test `test_store_forecasts_long_channel` asserts that the raw, untruncated channel is passed to `_store_forecasts` and verifying that:
  ```python
  raw_channel_sent = sess.added_forecasts[0]["source_channel"]
  assert len(raw_channel_sent) == 110  # Untruncated!
  ```
- In `src/geoanalytics/processing/common.py` (lines 307-308), `_store_forecasts` passes `source_channel=channel` directly to `repo.add_forecast`.
- In `src/geoanalytics/storage/models.py` (line 744), the `Forecast.source_channel` column is mapped to `String(64)`:
  ```python
      source_channel: Mapped[str | None] = mapped_column(String(64))
  ```
- In `src/geoanalytics/processing/reprocessing.py` (lines 61-68), `relink_existing` performs:
  ```python
      with session_scope() as session:
          index = EntityIndex(session)
          asset_cache = _load_asset_cache(session)
          embedder = get_embedder()
          have_embedding = set(session.scalars(select(Embedding.article_id)))
          to_embed: list[tuple[int, str]] = []
          articles = list(session.scalars(select(Article).limit(batch_size)))
  ```
  It has no pagination offset loop or query parameters, and thus only queries the first batch of articles.
- In `src/geoanalytics/processing/pipeline.py` (lines 337-352), `reprocess_skipped` does not use pagination or bulk updates when resetting the `processed` flag:
  ```python
          for doc in session.scalars(stmt):
              doc.processed = False
  ```
- Grep search of `tests/` for `relink_existing` and `reprocess_skipped` yielded zero results, meaning they have zero test coverage.

## 2. Logic Chain
- Since `Forecast.source_channel` is limited to 64 characters in the database schema (`String(64)`), inserting a string of length 110 (as shown in the adversarial test observation) into a real PostgreSQL database will result in a database error (`value too long for type character varying(64)`). Thus, any forecast posts processed from long channel names will crash the database transaction.
- Since `relink_existing` only selects articles using `select(Article).limit(batch_size)` without applying offsets or sorting, multiple consecutive runs of the function will repeatedly retrieve the same first batch of articles (up to 2000), meaning the function cannot process articles beyond the first batch and will redundant-process the same dataset indefinitely.
- Since `reprocess_skipped` fetches all matching documents into session memory at once via `for doc in session.scalars(stmt):`, executing this on a database with a large number of skipped noise documents will load massive amounts of ORM objects, causing high memory usage and potential Out-Of-Memory (OOM) failures.
- Since `relink_existing` and `reprocess_skipped` are completely missing from the test suites, any regressions or bugs introduced into these administrative pipelines will go unnoticed by the current automated testing system.

## 3. Caveats
- Actual execution of database constraint violations was not run against a live PostgreSQL instance, but is inferred based on SQLAlchemy column definitions (`String(64)`) and PostgreSQL behavior.
- ML model inference and GPU loading were mocked during test runs, so actual hardware/model integration limits were not checked.

## 4. Conclusion
The refactored processing code contains a High-risk database crash vulnerability during forecast insertions (`source_channel` column overflow), a Medium-risk pagination logic defect in the `relink_existing` pipeline which blocks processing beyond 2000 articles, and a Medium-risk memory bloat threat in `reprocess_skipped`. The overall risk rating is **MEDIUM**. Fixes should truncate long channel strings, paginate `relink_existing`, and bulk-update or paginate `reprocess_skipped`.

## 5. Verification Method
- Execute the test suite using:
  `.venv/bin/pytest tests/test_processing.py tests/test_processing_adversarial.py tests/test_processing_stress.py`
- Inspect `tests/test_processing_adversarial.py` (lines 225-245) to see the untruncated assertion.
- Inspect `src/geoanalytics/processing/common.py` (lines 300-309) to verify that `source_channel` is passed untruncated to `add_forecast`.
- Inspect `src/geoanalytics/processing/reprocessing.py` (lines 61-70) to confirm the missing pagination offset loop in `relink_existing`.
