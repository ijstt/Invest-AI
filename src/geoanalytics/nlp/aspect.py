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

from config.settings import get_settings
from geoanalytics.core.logging import get_logger
from geoanalytics.nlp._seqcls import ModelConfig, ModelLoader

log = get_logger("nlp.aspect")

SALIENT, BACKGROUND = "salient", "background"


def encode_pair(aspect: str, text: str, max_chars: int = 1000) -> str:
    """Единый формат входа классификаторов: аспект-префикс + текст новости.

    `aspect` — человекочитаемое имя актива с тикером («Сбербанк (SBER)»):
    модель учится оценивать текст относительно НЕГО.
    """
    return f"{aspect}: {text[:max_chars]}"


_SENT_CFG = ModelConfig(
    name="aspect_sentiment",
    err_level="error",
    loaded_desc="aspect-sentiment: модель",
    fallback_desc="aspect-sentiment: НЕ ЗАГРУЗИЛСЯ (фолбэк на тональность статьи)",
    unconfigured_desc="aspect-sentiment: не настроен",
)

_SAL_CFG = ModelConfig(
    name="saliency",
    err_level="error",
    loaded_desc="saliency: модель",
    fallback_desc="saliency: НЕ ЗАГРУЗИЛСЯ (фолбэк на тональность статьи)",
    unconfigured_desc="saliency: не настроен",
)


_SENT_LOADER = ModelLoader(_SENT_CFG, lambda: get_settings().aspect_sentiment_adapter_path, log)
_SAL_LOADER = ModelLoader(_SAL_CFG, lambda: get_settings().saliency_adapter_path, log)


def _get_sentiment_model():
    return _SENT_LOADER.get_model()


def _get_saliency_model():
    return _SAL_LOADER.get_model()


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
    stat_sent, desc_sent = _SENT_LOADER.get_status()
    stat_sal, desc_sal = _SAL_LOADER.get_status()
    degraded = (stat_sent == "degraded" or stat_sal == "degraded")
    return ("degraded" if degraded else "ok"), f"{desc_sent}; {desc_sal}"
