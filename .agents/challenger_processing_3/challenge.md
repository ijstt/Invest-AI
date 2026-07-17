## Challenge Summary

**Overall risk assessment**: MEDIUM

## Challenges

### [Medium] Challenge 1: Lack of Truncation for `Forecast.source_channel`

- **Assumption challenged**: The raw `channel` string from `payload.get("channel")` is always shorter than 64 characters, or it is safely handled by the database.
- **Attack scenario**: A broker channel on Telegram has a name longer than 64 characters (e.g. `TelegramChannelNameExceedingSixtyFourCharactersTelegramChannelNameExceedingSixtyFourCharacters`). During news processing, when a forecast post is detected (`is_fc = True`), the pipeline calls `_store_forecasts(session, article.id, facts, salient_asset_ids, t_date, payload.get("channel"))`. Inside `_store_forecasts`, the untruncated `channel` is passed as `source_channel=channel`.
- **Blast radius**: The PostgreSQL database will raise a `StringDataRightTruncation` (SQLAlchemy `DataError`) constraint violation because `Forecast.source_channel` is mapped to `String(64)`. While the nested transaction (savepoint) prevents the entire batch from crashing, this individual raw document will fail ingestion, print error logs, and remain unprocessed.
- **Mitigation**: Update `_store_forecasts` in `src/geoanalytics/processing/common.py` to truncate `channel`:
  ```python
  added += repo.add_forecast(
      article_id=article_id, asset_id=asset_id, kind=fact.kind,
      value=fact.value, unit=fact.unit, target_date=target_date,
      source_channel=channel[:64] if channel else None,
  )
  ```

## Stress Test Results

- **`make_full_text` boundary conditions** → Correct formatting on empty, None, and whitespace-padded inputs → Correct formatting returned (`"Title.  Body"`, `"Title."`, `"Body"`) → **PASS**
- **`_embed_batch` size-mismatch validation** → Embedder returning mismatched number of vectors raises ValueError and triggers fallback → Falls back to `embed_one` per article successfully → **PASS**
- **`_embed_batch` embedder failure fallback** → Embedder throwing exception falls back to `embed_one` per article → Falls back to `embed_one` successfully → **PASS**
- **`_embed_batch` per-article failure recovery** → `embed_one` throwing exception on a single item does not discard other items in the batch → Logs warning and skips only the failed article, successfully inserting the rest → **PASS**
- **Database field truncations (`Article.title`, `Article.source_ref`, `Article.url`, `ArticleEntity.mention`)** → Pointers/attributes truncated to respective column lengths (1024, 64, 1024, 256) → Attributes correctly sliced and saved → **PASS**
- **Database field truncation (`Forecast.source_channel`)** → Long channel name (> 64 characters) passed to `_store_forecasts` is safely truncated → Value is passed untruncated, leading to database schema violation → **FAIL**

## Unchallenged Areas

- **Concurrency and Lock Contention**: The behaviour of `paginate_query` and batch processing under concurrent worker runs was not challenged because the scope of the tests was single-threaded integration and unit verification.
- **NLP Model Internal Failure Modes**: Inside model functions (`sentiment.analyze`, `classify.classify_event`, etc.), we assume they correctly return expected type/sentiment structures; deep neural network failure modes (e.g. NaN outputs or OOM) were not tested.
