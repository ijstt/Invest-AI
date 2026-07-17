# Challenge Report — NLP Refactoring Verification

## Challenge Summary

**Overall risk assessment**: MEDIUM

The refactoring of the NLP modules successfully eliminates copy-pasted adapter loading logic and properly exposes public APIs. However, the introduction of a centralized `ModelLoader` and `SeqClsRegistry` introduces minor risks related to exception handling and dynamic reloading that could cause runtime crashes or stale models in specific deployment scenarios. The entire test suite passes 100% (1215 tests passed, including new unit and integration tests covering the refactored logic, concurrent execution, and adversarial inputs).

---

## Challenges

### [High] Challenge 1: Lack of Exception Safety on Path Resolution and Settings Fetching

- **Assumption challenged**: The settings retrieval function (passed as `get_path_fn` to `ModelLoader`) is assumed to be exception-safe and to always return either a valid path string or `None`.
- **Attack scenario**: If the configuration system fails (e.g. corrupted `.env`, malformed configuration file, or environment variable parsing error), calling `get_path_fn()` (e.g. `lambda: get_settings().event_adapter_path`) will raise an exception (such as `RuntimeError` or `ValueError`).
- **Blast radius**: In `sentiment.py`, the model retrieval is wrapped in an explicit `try-except` block, ensuring a fallback to the lexicon-based analyzer. However, the modules `classify.py`, `aspect.py`, `significance.py`, and `temporal.py` do not handle exceptions raised during the evaluation of `get_path_fn()` or `get_model()`. As a result, any error during settings fetching will propagate and crash the main entry points (`classify_event`, `analyze_pair`, `predict_significance`, and `classify_temporal`), completely bypassing the rule-based and formula-based fallback strategies.
- **Mitigation**: Wrap the execution of `get_path_fn()` inside `ModelLoader.get_model()` and `ModelLoader.get_status()` in a `try-except` block to log the error and treat the path as `None`, allowing the modules to fall back gracefully.
  ```python
  def get_model(self) -> SeqClsAdapter | None:
      try:
          path = self.get_path_fn()
      except Exception as exc:
          self.logger.error(f"{self.config.name}_path_resolution_failed", error=str(exc))
          path = None
      return registry.get_model(path, self.config, self.logger)
  ```

### [Medium] Challenge 2: Dynamic Path Reconfiguration and Stale Cache

- **Assumption challenged**: The model configuration paths for the sequence classifiers are static and do not change after the first model load.
- **Attack scenario**: In environments that support dynamic configuration reloads or multi-tenant deployments, a model path setting might change at runtime.
- **Blast radius**: Because `SeqClsRegistry` caches the loaded model based strictly on `config.name` (e.g., `self._cache[config.name] = model`), the registry will continue returning the old model loaded from the previous path. The new model will not be loaded unless the entire process is restarted.
- **Mitigation**: Cache the models using a composite key consisting of both `(config.name, path)` or check if the requested path matches the path from which the cached model was loaded, invalidating the cache if they differ.

### [Low] Challenge 3: Global Registry Loading Contention

- **Assumption challenged**: Concurrent model loading during process initialization does not cause execution serialization.
- **Attack scenario**: Multiple threads call `get_model` for different sequence classification tasks (e.g., `event`, `saliency`, `significance`) concurrently on startup.
- **Blast radius**: The `SeqClsRegistry` uses a single global `self._lock` for all models. Thread execution will be serialized across all model loading operations. While this prevents CPU/memory spikes during startup, it can increase process startup latency if multiple large models are initialized. Once initialized, the cached fast-path completely bypasses the lock.
- **Mitigation**: Implement per-model locks using a dictionary of locks (e.g. `defaultdict(threading.Lock)`) to allow concurrent loading of different models.

---

## Stress Test Results

- **Concurreny Stress Test** → 20 concurrent threads calling `sentiment.analyze()` and `SeqClsRegistry.get_model()` → Verified thread-safety and lack of race conditions → **PASS**
- **Robustness Against Missing/Invalid Paths** → Checked file/directory checks and invalid paths → Verified `load_seqcls_adapter` falls back to `None` and logs error → **PASS**
- **Robustness Against Corrupted JSON** → Checked loading with empty or malformed `labels.json` → Checked missing `labels` or `base` keys → Verified correct exception handling and error logging → **PASS**
- **Incorrect Settings Fallback** → Checked `sentiment.py` behavior when settings raise `RuntimeError` → Verified graceful fallback to lexicon sentiment → **PASS**
- **Incorrect Settings Crash** (Challenged) → Checked `classify_event` and others with raising settings → Verified propagation of error and crash (fails fallback) → **FAIL**

---

## Unchallenged Areas

- **Hardware/VRAM allocation limitations**: We assumed that the host machine has enough memory to hold all sequence classifier models simultaneously if they are all configured. If multiple large adapters are loaded on a low-memory host, it may lead to OOM.
