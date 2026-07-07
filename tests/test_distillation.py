"""Тесты кода дистилляции значимости (Ф7): задача LLM-разметки + загрузчик full-FT.

Без сети, Ollama и тяжёлых моделей — только чистая логика (детектор формата каталога,
реестр задач/промптов, парсинг метки)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from geoanalytics.nlp._seqcls import SeqClsAdapter

_ROOT = Path(__file__).resolve().parent.parent


def _load_script(name: str):
    """Грузит scripts/<name>.py как модуль по пути (scripts/ не в sys.path)."""
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_llm_label():
    return _load_script("llm_label")


def test_significance_task_registered():
    mod = _load_llm_label()
    assert "significance" in mod.TASK_LABELS
    assert mod.TASK_LABELS["significance"] == ["low", "medium", "high"]
    assert "significance" in mod._PROMPTS
    assert "{text}" in mod._PROMPTS["significance"]


def test_parse_label_picks_first_significance_label():
    mod = _load_llm_label()
    allowed = mod.TASK_LABELS["significance"]
    assert mod._parse_label("Важность: high", allowed) == "high"
    assert mod._parse_label("это low значимость", allowed) == "low"
    # Первая встретившаяся метка побеждает (medium раньше high в тексте).
    assert mod._parse_label("medium, не high", allowed) == "medium"
    assert mod._parse_label("ответ непонятен", allowed) is None


def test_events_task_registered():
    from geoanalytics.core.types import EventType

    mod = _load_llm_label()
    assert "events" in mod.TASK_LABELS
    assert "events" in mod._PROMPTS
    assert "{text}" in mod._PROMPTS["events"]
    # Метки соответствуют EventType, кроме служебных news/market.
    labels = set(mod.TASK_LABELS["events"])
    event_values = {e.value for e in EventType}
    assert labels == event_values
    # Парсинг извлекает категорию из ответа LLM.
    allowed = mod.TASK_LABELS["events"]
    assert mod._parse_label("Категория: sanctions", allowed) == "sanctions"
    assert mod._parse_label("это noise", allowed) == "noise"


def test_augment_parse_batch_cleans_and_filters():
    aug = _load_script("augment_gold")
    response = (
        "1. Газпром отчитался о росте чистой прибыли на 20% за квартал.\n"
        "- Сбербанк увеличил выручку до рекордных значений по итогам года.\n"
        "\n"
        "коротко\n"
        "• Лукойл сообщил о снижении EBITDA на фоне падения цен на нефть.\n"
    )
    items = aug.parse_batch(response)
    # Маркеры/нумерация срезаны, пустые и слишком короткие отброшены.
    assert len(items) == 3
    assert items[0].startswith("Газпром отчитался")
    assert all(line[0:2] not in ("- ", "• ") for line in items)
    assert "коротко" not in items


def test_is_full_model_detects_full_vs_adapter(tmp_path):
    # Полная модель: config.json без adapter_config.json.
    full = tmp_path / "full"
    full.mkdir()
    (full / "config.json").write_text("{}", encoding="utf-8")
    assert SeqClsAdapter._is_full_model(str(full)) is True

    # LoRA-адаптер: есть adapter_config.json.
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    (adapter / "config.json").write_text("{}", encoding="utf-8")
    (adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
    assert SeqClsAdapter._is_full_model(str(adapter)) is False

    # Пустой/неполный каталог — не полная модель.
    assert SeqClsAdapter._is_full_model(str(tmp_path / "missing")) is False
