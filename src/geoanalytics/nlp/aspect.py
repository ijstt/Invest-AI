"""F1+F2 (Волна 2): aspect-тональность и салиентность пары (статья, актив).

Б2: у статьи одна тональность, копируемая во все связи статья↔актив — для
мультиактивных новостей это ошибка («Сбер обыграл ВТБ по марже» — позитив SBER,
негатив VTBR). Здесь два дообученных классификатора (full-FT tiny2, золото —
Qwen с промптом «относительно компании X», scripts/llm_label_aspect.py):

    aspect-sentiment — тональность новости ИМЕННО для актива (positive/neutral/negative);
    saliency         — актив главный объект новости (salient) или фон (background).

Кодировка входа ЕДИНАЯ для обучения и инференса: `encode_pair`. Graceful: модель
не настроена/не загрузилась → None, конвейер падает на копию тональности статьи
(поведение до F1) — деградация видна в health-check (I4).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from config.settings import get_settings
from geoanalytics.core.logging import get_logger

log = get_logger("nlp.aspect")

SALIENT, BACKGROUND = "salient", "background"


def encode_pair(aspect: str, text: str, max_chars: int = 1000) -> str:
    """Единый формат входа классификаторов: аспект-префикс + текст новости.

    `aspect` — человекочитаемое имя актива с тикером («Сбербанк (SBER)»):
    модель учится оценивать текст относительно НЕГО.
    """
    return f"{aspect}: {text[:max_chars]}"


def _load(path: str | None, name: str):
    """Загрузка SeqClsAdapter с громкой деградацией (как у significance, Б1)."""
    if not path:
        return None
    if not Path(path).exists():
        log.error(f"{name}_adapter_missing_FALLBACK", path=path)
        return None
    try:
        from geoanalytics.nlp._seqcls import SeqClsAdapter

        model = SeqClsAdapter(path)
        log.info(f"{name}_model_ready", path=path)
        return model
    except Exception as exc:  # noqa: BLE001 — конвейер живёт на фолбэке, но громко
        log.error(f"{name}_model_failed_FALLBACK", error=str(exc))
        return None


@lru_cache
def _get_sentiment_model():
    return _load(get_settings().aspect_sentiment_adapter_path, "aspect_sentiment")


@lru_cache
def _get_saliency_model():
    return _load(get_settings().saliency_adapter_path, "saliency")


def analyze_pair(aspect: str, text: str) -> tuple[str | None, bool | None]:
    """(тональность для актива | None, салиентность | None) — None = модели нет/упала.

    Вызывающий код при None падает на поведение до F1/F2 (копия тональности
    статьи; салиентность неизвестна → связь считается салиентной).
    """
    encoded = encode_pair(aspect, text)
    sentiment: str | None = None
    salient: bool | None = None
    model = _get_sentiment_model()
    if model is not None:
        try:
            sentiment = model.predict_label(encoded)
        except Exception as exc:  # noqa: BLE001
            log.error("aspect_sentiment_predict_failed_FALLBACK", error=str(exc))
    sal_model = _get_saliency_model()
    if sal_model is not None:
        try:
            salient = sal_model.predict_label(encoded) == SALIENT
        except Exception as exc:  # noqa: BLE001
            log.error("saliency_predict_failed_FALLBACK", error=str(exc))
    return sentiment, salient


def aspect_name(ticker: str, name: str | None) -> str:
    """Аспект-строка актива — как в разметке золота (llm_label_aspect)."""
    return f"{name} ({ticker})" if name and name != ticker else ticker


def model_status() -> tuple[str, str]:
    """Статус F1/F2 для health-check: degraded, если настроено, но не загрузилось."""
    s = get_settings()
    parts: list[str] = []
    degraded = False
    for path, model, label in (
        (s.aspect_sentiment_adapter_path, _get_sentiment_model(), "aspect-sentiment"),
        (s.saliency_adapter_path, _get_saliency_model(), "saliency"),
    ):
        if not path:
            parts.append(f"{label}: не настроен")
        elif model is None:
            parts.append(f"{label}: НЕ ЗАГРУЗИЛСЯ (фолбэк на тональность статьи)")
            degraded = True
        else:
            parts.append(f"{label}: модель")
    return ("degraded" if degraded else "ok"), "; ".join(parts)
