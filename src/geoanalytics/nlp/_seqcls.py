"""Общий загрузчик дообученных seq-классификаторов (M6.5).

Используется классификаторами событий (`classify`) и значимости (`significance`):
предсказывают строковую метку. Тяжёлые зависимости (torch/transformers/peft)
импортируются лениво — модуль грузится без extras `[train]`/`[nlp]`, а инференс
включается только при наличии адаптера/модели.

Поддерживаются два формата каталога (как сохраняет `scripts/train_lora.py`):
- LoRA-адаптер: веса адаптера + токенайзер + `labels.json`
  ({"labels": [...], "base": "<имя базовой модели>"}); грузится поверх базы;
- полностью дообученная модель (`--full-finetune`): `config.json` + веса + токенайзер +
  `labels.json`; грузится напрямую, без базы и PEFT (рецепт дистилляции LLM-учителя,
  как у sentiment M7 — LoRA-поверх-frozen упирается в потолок).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def is_full_model(path: str | Path) -> bool:
    """Каталог — полностью дообученная модель (config.json без adapter_config.json),
    а не PEFT-адаптер (adapter_config.json)."""
    p = Path(path)
    return (p / "config.json").exists() and not (p / "adapter_config.json").exists()


class SeqClsAdapter:
    """Загруженный seq-классификатор: text → строковая метка (argmax логитов)."""

    @staticmethod
    def _is_full_model(path: str) -> bool:
        """Каталог — полностью дообученная модель (config.json без adapter_config.json),
        а не PEFT-адаптер (adapter_config.json)."""
        return is_full_model(path)

    def __init__(self, adapter_path: str) -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        meta = json.loads((Path(adapter_path) / "labels.json").read_text(encoding="utf-8"))
        self.labels: list[str] = meta["labels"]
        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(adapter_path)
        if self._is_full_model(adapter_path):
            # Полная модель: каталог содержит config.json + веса + токенайзер.
            self._model = AutoModelForSequenceClassification.from_pretrained(adapter_path)
        else:
            from peft import PeftModel

            id2label = dict(enumerate(self.labels))
            label2id = {lab: i for i, lab in id2label.items()}
            model = AutoModelForSequenceClassification.from_pretrained(
                meta["base"], num_labels=len(self.labels),
                id2label=id2label, label2id=label2id, ignore_mismatched_sizes=True,
            )
            self._model = PeftModel.from_pretrained(model, adapter_path)
        self._model.eval()

    def predict_label(self, text: str, max_length: int = 256) -> str:
        inputs = self._tokenizer(text, return_tensors="pt", truncation=True,
                                 max_length=max_length)
        with self._torch.no_grad():
            logits = self._model(**inputs).logits
        return self.labels[int(logits[0].argmax())]


def load_seqcls_adapter(
    path: str | None,
    logger: Any,
    *,
    name: str,
    err_level: str = "error",
    missing_key: str | None = None,
    ready_key: str | None = None,
    failed_key: str | None = None,
) -> SeqClsAdapter | None:
    """Общий загрузчик SeqClsAdapter с ленивым импортом и кастомным логированием."""
    if not path:
        return None
    try:
        exists = Path(path).exists()
    except Exception as exc:
        msg = failed_key or f"{name}_model_failed_FALLBACK"
        if err_level == "warning":
            logger.warning(msg, error=str(exc))
        else:
            logger.error(msg, error=str(exc))
        return None

    if not exists:
        msg = missing_key or f"{name}_adapter_missing_FALLBACK"
        if err_level == "warning":
            logger.warning(msg, path=path)
        else:
            logger.error(msg, path=path)
        return None
    try:
        clf = SeqClsAdapter(path)
        logger.info(ready_key or f"{name}_model_ready", path=path)
        return clf
    except Exception as exc:  # noqa: BLE001
        msg = failed_key or f"{name}_model_failed_FALLBACK"
        if err_level == "warning":
            logger.warning(msg, error=str(exc))
        else:
            logger.error(msg, error=str(exc))
        return None


@dataclass(frozen=True)
class ModelConfig:
    name: str
    err_level: str = "error"
    missing_key: str | None = None
    ready_key: str | None = None
    failed_key: str | None = None
    loaded_desc: str = "модель загружена"
    fallback_desc: str = "фолбэк активен"
    unconfigured_desc: str = "не настроен"


class SeqClsRegistry:
    def __init__(self) -> None:
        self._cache: dict[str, SeqClsAdapter | None] = {}
        self._lock = threading.Lock()

    def get_model(self, path: str | None, config: ModelConfig, logger: Any) -> SeqClsAdapter | None:
        if config.name not in self._cache:
            with self._lock:
                if config.name not in self._cache:
                    self._cache[config.name] = load_seqcls_adapter(
                        path,
                        logger,
                        name=config.name,
                        err_level=config.err_level,
                        missing_key=config.missing_key,
                        ready_key=config.ready_key,
                        failed_key=config.failed_key,
                    )
        return self._cache[config.name]

    def get_status(self, path: str | None, config: ModelConfig, logger: Any) -> tuple[str, str]:
        configured = bool(path)
        model = self.get_model(path, config, logger)
        if model is not None:
            return "ok", config.loaded_desc
        if configured:
            return "degraded", config.fallback_desc
        return "ok", config.unconfigured_desc


registry = SeqClsRegistry()


class ModelLoader:
    """Универсальный загрузчик дообученных классификаторов с поддержкой реестра."""

    def __init__(self, config: ModelConfig, get_path_fn: Any, logger: Any) -> None:
        self.config = config
        self.get_path_fn = get_path_fn
        self.logger = logger

    def get_model(self) -> SeqClsAdapter | None:
        return registry.get_model(self.get_path_fn(), self.config, self.logger)

    def get_status(self) -> tuple[str, str]:
        return registry.get_status(self.get_path_fn(), self.config, self.logger)
