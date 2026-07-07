"""Тональность русского текста.

Двухуровневая стратегия:
1) основной путь — модель ruBERT (transformers) для финансово-новостного сентимента;
2) фолбэк — лёгкий лексиконный анализатор (без torch), чтобы конвейер работал
   даже без тяжёлых зависимостей и на слабом железе.

Возвращает (label, score), где score ∈ [-1, 1] (отрицательный → негатив).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from config.settings import get_settings
from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import Sentiment

log = get_logger("nlp.sentiment")

# --- Лексикон для фолбэка (минимальный, расширяемый) ---
_POSITIVE = {
    "рост", "вырос", "выросли", "прибыль", "рекорд", "увеличил", "увеличение",
    "превысил", "улучшил", "позитив", "поддержка", "дивиденды", "успех", "укрепил",
    "восстановление", "оптимизм", "подъём", "рекордный", "повысил",
}
_NEGATIVE = {
    "падение", "упал", "упали", "снижение", "снизил", "убыток", "санкции", "кризис",
    "обвал", "дефолт", "сократил", "сокращение", "потери", "риск", "угроза",
    "ослабил", "негатив", "проблемы", "штраф", "запрет", "просадка", "девальвация",
}
_WORD_RE = re.compile(r"[а-яё]+", re.IGNORECASE)


def _label_from_score(score: float) -> Sentiment:
    if score > 0.15:
        return Sentiment.POSITIVE
    if score < -0.15:
        return Sentiment.NEGATIVE
    return Sentiment.NEUTRAL


def _lexicon_sentiment(text: str) -> tuple[Sentiment, float]:
    """Простой подсчёт по словарю позитив/негатив."""
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if not words:
        return Sentiment.NEUTRAL, 0.0
    pos = sum(1 for w in words if w in _POSITIVE)
    neg = sum(1 for w in words if w in _NEGATIVE)
    total = pos + neg
    if total == 0:
        return Sentiment.NEUTRAL, 0.0
    score = (pos - neg) / total
    return _label_from_score(score), round(score, 3)


class _RubertSentiment:
    """Ленивая обёртка над моделью ruBERT."""

    # Родной порядок меток базы blanchefort/rubert-base-cased-sentiment (без адаптера).
    _BASE_LABELS = [Sentiment.NEUTRAL, Sentiment.POSITIVE, Sentiment.NEGATIVE]

    def __init__(self, model_name: str, adapter_path: str | None = None) -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._torch = torch
        # Активен ли дообученный адаптер/FT-модель (для health-check I4: база без
        # адаптера — деградация, если адаптер настроен).
        self.adapter_active = bool(adapter_path)
        # Порядок меток в логитах модели (индекс → Sentiment).
        self._labels = list(self._BASE_LABELS)
        if adapter_path and self._is_full_model(adapter_path):
            # Полностью дообученная модель (--full-finetune): дистилляция LLM-учителя.
            # LoRA-поверх-frozen blanchefort упирался в потолок (признаки базы расходятся
            # с метками учителя), поэтому sentiment дообучается целиком. Каталог содержит
            # config.json + веса + свой токенайзер — грузим его напрямую, базовую модель
            # не трогаем. Порядок меток — из labels.json (sorted, как в train_lora.py).
            self._tokenizer = AutoTokenizer.from_pretrained(adapter_path)
            model = AutoModelForSequenceClassification.from_pretrained(adapter_path)
            self._labels = self._load_adapter_labels(adapter_path)
            log.info(
                "sentiment_finetuned_loaded",
                path=adapter_path,
                labels=[s.value for s in self._labels],
            )
        elif adapter_path:
            # Дообученный LoRA-адаптер (M4) накладывается поверх базовой ruBERT.
            # ВАЖНО: train_lora.py строит метки через sorted(...) и переопределяет голову,
            # поэтому порядок логитов адаптера НЕ совпадает с _BASE_LABELS. Берём фактический
            # порядок из labels.json адаптера (как в nlp/_seqcls.py), иначе тональности
            # перепутаются.
            from peft import PeftModel

            self._tokenizer = AutoTokenizer.from_pretrained(model_name)
            model = AutoModelForSequenceClassification.from_pretrained(model_name)
            self._labels = self._load_adapter_labels(adapter_path)
            model = PeftModel.from_pretrained(model, adapter_path)
            log.info(
                "sentiment_adapter_loaded",
                path=adapter_path,
                labels=[s.value for s in self._labels],
            )
        else:
            self._tokenizer = AutoTokenizer.from_pretrained(model_name)
            model = AutoModelForSequenceClassification.from_pretrained(model_name)
        # Индексы позитива/негатива для расчёта score (по имени метки, не по позиции).
        self._pos_idx = self._labels.index(Sentiment.POSITIVE)
        self._neg_idx = self._labels.index(Sentiment.NEGATIVE)
        self._model = model
        self._model.eval()

    @staticmethod
    def _is_full_model(path: str) -> bool:
        """Каталог — полностью дообученная модель (config.json без adapter_config.json),
        а не PEFT-адаптер (adapter_config.json)."""
        p = Path(path)
        return (p / "config.json").exists() and not (p / "adapter_config.json").exists()

    @staticmethod
    def _load_adapter_labels(adapter_path: str) -> list[Sentiment]:
        """Читает порядок строковых меток из labels.json адаптера → список Sentiment."""
        import json

        meta = json.loads((Path(adapter_path) / "labels.json").read_text(encoding="utf-8"))
        return [Sentiment(label) for label in meta["labels"]]

    def predict(self, text: str) -> tuple[Sentiment, float]:
        inputs = self._tokenizer(
            text, return_tensors="pt", truncation=True, max_length=512
        )
        with self._torch.no_grad():
            logits = self._model(**inputs).logits
        probs = self._torch.softmax(logits, dim=1)[0]
        idx = int(probs.argmax())
        label = self._labels[idx]
        # score: positive prob минус negative prob (индексы — по имени метки).
        score = float(probs[self._pos_idx] - probs[self._neg_idx])
        return label, round(score, 3)


@lru_cache
def _get_model() -> _RubertSentiment | None:
    settings = get_settings()
    model_name = settings.sentiment_model
    adapter = settings.sentiment_adapter_path
    if adapter and not Path(adapter).exists():
        log.warning("sentiment_adapter_missing", path=adapter)
        adapter = None
    try:
        model = _RubertSentiment(model_name, adapter)
        log.info("sentiment_ready", backend="rubert", model=model_name, adapter=bool(adapter))
        return model
    except Exception as exc:  # noqa: BLE001 — модель/адаптер опциональны, есть фолбэк
        # Если не удалось именно с адаптером — пробуем базовую модель без него,
        # прежде чем падать в лексиконный фолбэк.
        if adapter:
            log.warning("sentiment_adapter_failed_base", error=str(exc))
            try:
                model = _RubertSentiment(model_name, None)
                log.info("sentiment_ready", backend="rubert", model=model_name, adapter=False)
                return model
            except Exception as exc2:  # noqa: BLE001
                log.warning("sentiment_fallback_to_lexicon", error=str(exc2))
                return None
        log.warning("sentiment_fallback_to_lexicon", error=str(exc))
        return None


def model_status() -> tuple[str, str]:
    """Статус тональности для health-check (I4): ("ok"|"degraded", деталь).

    degraded — каскад работает не тем уровнем, под который откалиброваны потребители:
    лексиконный фолбэк вместо модели или база без настроенного адаптера.
    """
    configured = bool(get_settings().sentiment_adapter_path)
    model = _get_model()
    if model is None:
        return "degraded", "лексиконный фолбэк (модель не загрузилась)"
    if configured and not model.adapter_active:
        return "degraded", "база без адаптера (адаптер настроен, но не загрузился)"
    return "ok", "rubert" + (" + дообученная модель" if model.adapter_active else " (база)")


def analyze(text: str) -> tuple[Sentiment, float]:
    """Тональность текста: (метка, score ∈ [-1, 1])."""
    if not text.strip():
        return Sentiment.NEUTRAL, 0.0
    model = _get_model()
    if model is None:
        return _lexicon_sentiment(text)
    try:
        return model.predict(text)
    except Exception as exc:  # noqa: BLE001
        log.warning("sentiment_failed_fallback", error=str(exc))
        return _lexicon_sentiment(text)
