# NLP API Analysis and Signature Compatibility Map

This document presents a comprehensive analysis of the NLP APIs in `src/geoanalytics/nlp/`, specifically focusing on module exports/imports, public APIs that must be preserved, a signature compatibility map, and a strategy to enforce file size constraints.

---

## 1. Imports and Exports in `nlp/fundamentals.py` and `nlp/numeric.py`

### 1.1 `nlp/numeric.py`

#### Imports
- `from __future__ import annotations`
- `import re`
- `from dataclasses import dataclass`

#### Exports (Public API)
- **Constants & Variables**:
  - `DIVIDEND: str = "dividend"`
  - `KEY_RATE: str = "key_rate"`
  - `DEAL_AMOUNT: str = "deal_amount"`
  - `TARGET_PRICE: str = "target_price"`
  - `KINDS: tuple[str, ...] = ("dividend", "key_rate", "deal_amount", "target_price")`
  - `MULT: dict[str, float] = {"тыс": 1e3, "млн": 1e6, "млрд": 1e9, "трлн": 1e12}`
- **Data Classes**:
  - `NumericFact`:
    - Fields: `kind: str`, `value: float`, `unit: str`, `snippet: str`
- **Functions**:
  - `to_float(raw: str) -> float`
  - `extract_numbers(text: str) -> list[NumericFact]`

#### Private & Internal Defs (including requested aliases)
- `_NUM: str` (Regex string component for matching numbers)
- `_MULT: dict[str, float] = MULT` (Private alias to the multiplier dictionary)
- `_CUR_SYMBOL: dict[str, str] = {"$": "USD", "€": "EUR", "₽": "RUB"}` (Currency symbol lookup)
- `_DIV_RE: re.Pattern` (Compiled regex for dividend detection)
- `_RATE_RE: re.Pattern` (Compiled regex for key rate detection)
- `_FORECAST_RE: re.Pattern` (Compiled regex for identifying forecasted rate statements)
- `_RATE_WINDOW: int = 80` (Character window search limit)
- `_AMOUNT_RE: re.Pattern` (Compiled regex for deal amount detection)
- `_DEAL_TRIGGER_RE: re.Pattern` (Compiled regex for deal-identifying verbs)
- `_DEAL_WINDOW: int = 100` (Character window for transaction verification)
- `_TARGET_RE: re.Pattern` (Compiled regex for target price detection)
- `_SNIPPET_MAX: int = 200` (Character limit for snippets)
- `_to_float: callable = to_float` (Private alias to `to_float`)
- `_currency(symbol: str | None, word: str | None) -> str | None` (Helper to resolve currency codes)
- `_snippet(text: str, m: re.Match) -> str` (Helper to extract matches)

---

### 1.2 `nlp/fundamentals.py`

#### Imports
- `from __future__ import annotations`
- `import re`
- `from dataclasses import dataclass`
- `from geoanalytics.nlp.numeric import MULT, to_float` (Imports public version of multiplier map and parser function; **does not use the private `_MULT` or `_to_float` aliases**)

#### Exports (Public API)
- **Data Classes**:
  - `FundamentalFact`:
    - Fields: `metric: str`, `value: float`, `unit: str`, `period: str | None`, `snippet: str`
- **Functions**:
  - `detect_period(text: str) -> str | None`
  - `extract_fundamentals(text: str, *, period: str | None = None) -> list[FundamentalFact]`

#### Private & Internal Defs
- `_SNIPPET_MAX: int = 160` (Character limit for snippets)
- `_WINDOW: int = 90` (Window size after metric trigger to search for values)
- `_TRIGGERS: dict[str, tuple[str, ...]]` (Dictionary mapping metric name to list of Russian word roots)
- `_AMOUNT_SCALED: re.Pattern` (Regex for general scaled financial amounts)
- `_PER_SHARE: re.Pattern` (Regex for amount per share)
- `_PE: re.Pattern` (Regex for P/E ratio)
- `_CUR: tuple[tuple[str, str], ...]` (Currency mapping table)
- `_currency(word: str) -> str` (Helper resolving currency code from matched unit)
- `_match_metric(metric: str, trig: str, window: str, period: str | None) -> FundamentalFact | None` (Helper matching specific metric parameters)

---

## 2. Public and Internal APIs to Preserve in Other Modules

We must preserve not only the public API but also private components that tests and scripts access or mock directly.

### 2.1 `nlp/sentiment.py`
- **Public API**:
  - `analyze(text: str) -> tuple[Sentiment, float]`
  - `model_status() -> tuple[str, str]`
- **Internal / Test-Mocked API**:
  - `_lexicon_sentiment(text: str) -> tuple[Sentiment, float]` (Tested directly in `tests/test_nlp.py`)
  - `_RubertSentiment` (Class wrapping the transformers model)
  - `_get_model() -> _RubertSentiment | None` (LRU-cached model retriever)

### 2.2 `nlp/classify.py`
- **Public API**:
  - `classify_event(text: str) -> EventType`
  - `model_status() -> tuple[str, str]`
- **Internal / Test-Mocked API**:
  - `_get_classifier() -> SeqClsAdapter | None` (Mocked in `tests/test_nlp.py` and imported by `scripts/eval_events.py`)
  - `_classify_by_rules(text: str) -> EventType` (Imported by `scripts/eval_events.py`)
  - `_label_to_event(label: str) -> EventType` (Tested directly in `tests/test_nlp.py`)

### 2.3 `nlp/significance.py`
- **Public API**:
  - Constants: `EVENT_WEIGHT: dict[str, float]`, `DEFAULT_W_TYPE: float = 0.5`, `DEFAULT_W_SENT: float = 0.3`, `DEFAULT_W_LINK: float = 0.2`, `SIG_BUCKETS: tuple[str, ...] = ("low", "medium", "high")`
  - Functions:
    - `type_weight(event_type: str | None) -> float`
    - `significance_score(event_type: str | None, sentiment_score: float | None, link_relevances: Iterable[float] | None = None, *, w_type: float = DEFAULT_W_TYPE, w_sent: float = DEFAULT_W_SENT, w_link: float = DEFAULT_W_LINK) -> float`
    - `significance_bucket(value: float, low: float = 0.34, high: float = 0.66) -> str`
    - `significance_gates(settings=None) -> dict[str, float]`
    - `validate_cascade(settings=None) -> list[str]`
    - `model_status() -> tuple[str, str]`
    - `predict_significance(text: str) -> float | None`
- **Internal / Test-Mocked API**:
  - `_BUCKET_VALUE: dict[str, float] = {"low": 0.15, "medium": 0.5, "high": 0.85, "flat": 0.15, "moved": 0.85}` (Imported by `tests/test_dataset.py`)
  - `_get_model()` (Mocked in `tests/test_nlp.py` and imported by `scripts/eval_significance.py`)

### 2.4 `nlp/temporal.py`
- **Public API**:
  - Constants: `PAST = "past"`, `FUTURE = "future"`, `FORECAST = "forecast"`, `NONE = "none"`, `LABELS = ("past", "future", "forecast", "none")`
  - Functions:
    - `extract_event_dates(text: str, published: date) -> list[date]`
    - `anchor_event_date(dates: list[date], published: date, status: str) -> date | None`
    - `classify_temporal(text: str) -> str | None`
    - `model_status() -> tuple[str, str]`
    - `temporal_anchor(text: str, published: date) -> tuple[str | None, date | None]`
- **Internal / Test-Mocked API**:
  - `_model()` (LRU-cached sequence classifier adapter)

### 2.5 `nlp/aspect.py`
- **Public API**:
  - Constants: `SALIENT = "salient"`, `BACKGROUND = "background"`
  - Functions:
    - `encode_pair(aspect: str, text: str, max_chars: int = 1000) -> str`
    - `analyze_pair(aspect: str, text: str) -> tuple[str | None, bool | None]`
    - `aspect_name(ticker: str, name: str | None) -> str`
    - `model_status() -> tuple[str, str]`
- **Internal / Test-Mocked API**:
  - `_get_sentiment_model()` (Mocked in `tests/test_aspect.py`)
  - `_get_saliency_model()` (Mocked in `tests/test_aspect.py`)

---

## 3. Strict Signature Compatibility Map

Below is a strict Python-typed specification of the APIs. Any modifications must match these types exactly.

| Module | Name | Type | Signature / Value |
| :--- | :--- | :--- | :--- |
| **`nlp/numeric`** | `DIVIDEND` | Constant | `"dividend"` |
| | `KEY_RATE` | Constant | `"key_rate"` |
| | `DEAL_AMOUNT` | Constant | `"deal_amount"` |
| | `TARGET_PRICE` | Constant | `"target_price"` |
| | `KINDS` | Constant | `("dividend", "key_rate", "deal_amount", "target_price")` |
| | `MULT` | Constant | `{"тыс": 1e3, "млн": 1e6, "млрд": 1e9, "трлн": 1e12}` |
| | `_MULT` | Constant | Alias to `MULT` |
| | `NumericFact` | Class | `dataclass(frozen=True)` with fields: `kind: str`, `value: float`, `unit: str`, `snippet: str` |
| | `to_float` | Function | `(raw: str) -> float` |
| | `_to_float` | Function | Alias to `to_float` |
| | `extract_numbers` | Function | `(text: str) -> list[NumericFact]` |
| **`nlp/fundamentals`**| `FundamentalFact` | Class | `dataclass(frozen=True)` with fields: `metric: str`, `value: float`, `unit: str`, `period: str \| None`, `snippet: str` |
| | `detect_period` | Function | `(text: str) -> str \| None` |
| | `extract_fundamentals`| Function | `(text: str, *, period: str \| None = None) -> list[FundamentalFact]` |
| **`nlp/sentiment`** | `model_status` | Function | `() -> tuple[str, str]` |
| | `analyze` | Function | `(text: str) -> tuple[Sentiment, float]` (where Sentiment is `geoanalytics.core.types.Sentiment`) |
| | `_lexicon_sentiment` | Function | `(text: str) -> tuple[Sentiment, float]` |
| **`nlp/classify`** | `model_status` | Function | `() -> tuple[str, str]` |
| | `classify_event` | Function | `(text: str) -> EventType` (where EventType is `geoanalytics.core.types.EventType`) |
| | `_get_classifier` | Function | `() -> SeqClsAdapter \| None` |
| | `_classify_by_rules` | Function | `(text: str) -> EventType` |
| | `_label_to_event` | Function | `(label: str) -> EventType` |
| **`nlp/significance`**| `EVENT_WEIGHT` | Constant | `dict[str, float]` mapping EventType values to weights |
| | `DEFAULT_W_TYPE` | Constant | `0.5` |
| | `DEFAULT_W_SENT` | Constant | `0.3` |
| | `DEFAULT_W_LINK` | Constant | `0.2` |
| | `SIG_BUCKETS` | Constant | `("low", "medium", "high")` |
| | `_BUCKET_VALUE` | Constant | `{"low": 0.15, "medium": 0.5, "high": 0.85, "flat": 0.15, "moved": 0.85}` |
| | `type_weight` | Function | `(event_type: str \| None) -> float` |
| | `significance_score`| Function | `(event_type: str \| None, sentiment_score: float \| None, link_relevances: Iterable[float] \| None = None, *, w_type: float = DEFAULT_W_TYPE, w_sent: float = DEFAULT_W_SENT, w_link: float = DEFAULT_W_LINK) -> float` |
| | `significance_bucket`| Function | `(value: float, low: float = 0.34, high: float = 0.66) -> str` |
| | `significance_gates` | Function | `(settings=None) -> dict[str, float]` |
| | `validate_cascade` | Function | `(settings=None) -> list[str]` |
| | `model_status` | Function | `() -> tuple[str, str]` |
| | `predict_significance`| Function | `(text: str) -> float \| None` |
| | `_get_model` | Function | `() -> SeqClsAdapter \| None` |
| **`nlp/temporal`** | `PAST` | Constant | `"past"` |
| | `FUTURE` | Constant | `"future"` |
| | `FORECAST` | Constant | `"forecast"` |
| | `NONE` | Constant | `"none"` |
| | `LABELS` | Constant | `("past", "future", "forecast", "none")` |
| | `extract_event_dates`| Function | `(text: str, published: date) -> list[date]` |
| | `anchor_event_date` | Function | `(dates: list[date], published: date, status: str) -> date \| None` |
| | `classify_temporal` | Function | `(text: str) -> str \| None` |
| | `model_status` | Function | `() -> tuple[str, str]` |
| | `temporal_anchor` | Function | `(text: str, published: date) -> tuple[str \| None, date \| None]` |
| **`nlp/aspect`** | `SALIENT` | Constant | `"salient"` |
| | `BACKGROUND` | Constant | `"background"` |
| | `encode_pair` | Function | `(aspect: str, text: str, max_chars: int = 1000) -> str` |
| | `analyze_pair` | Function | `(aspect: str, text: str) -> tuple[str \| None, bool \| None]` |
| | `aspect_name` | Function | `(ticker: str, name: str \| None) -> str` |
| | `model_status` | Function | `() -> tuple[str, str]` |
| | `_get_sentiment_model`| Function | `() -> SeqClsAdapter \| None` |
| | `_get_saliency_model`| Function | `() -> SeqClsAdapter \| None` |

---

## 4. File Length Management Strategy

To ensure that no created or modified files exceed **600 lines** (all current NLP files are well under 200 lines), subsequent agents must follow this strategy:

1. **Rule of Single Responsibility (Functional vs Model Loading)**:
   - For modules utilizing heavy library calls or model wrappers (e.g. `transformers`, `torch`), separate the rule-based logic (regex, dict lookups, math formula evaluations) from the loading/inference wrappers.
   - Example: If `sentiment.py` grows close to 500 lines, extract `_RubertSentiment` into a `models/sentiment_model.py` file, leaving only the lexicon-fallback and `analyze` routing logic in `sentiment.py`.

2. **Configuration/Weights Externalization**:
   - Rather than storing large trigger lists or weight configuration constants inline, load them from settings or a clean data lookup file if they expand significantly.
   - Already, settings are managed via `config.settings.get_settings()`, which should remain the source of truth.

3. **CI/CD & Git Pre-commit Enforcement**:
   - Integrate a file line count check in the local developer environment.
   - Run the following check locally or within a git hook to fail early if any source file exceeds the 600-line limit:
     ```bash
     find src/geoanalytics/nlp/ -name "*.py" | xargs wc -l | awk '$1 > 600 { print "Error: " $2 " exceeds 600 lines limit (" $1 " lines)" ; exit 1 }'
     ```

4. **Common Model Loading Facade (`_seqcls.py`)**:
   - Avoid repeating adapter load code. All classification loading rules (e.g., config error handling, fallback logging) must remain consolidated in `nlp/_seqcls.py`.
