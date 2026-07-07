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
from pathlib import Path


class SeqClsAdapter:
    """Загруженный seq-классификатор: text → строковая метка (argmax логитов)."""

    @staticmethod
    def _is_full_model(path: str) -> bool:
        """Каталог — полностью дообученная модель (config.json без adapter_config.json),
        а не PEFT-адаптер (adapter_config.json)."""
        p = Path(path)
        return (p / "config.json").exists() and not (p / "adapter_config.json").exists()

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
