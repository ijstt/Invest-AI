"""Классификация новостного события по категориям (EventType).

Два уровня (как у сентимента):
1) основной путь — дообученный LoRA-классификатор (M6.5), если задан
   `GEO_EVENT_ADAPTER_PATH` и модель грузится;
2) фолбэк — правила по ключевым словам (быстро, прозрачно, без моделей).

Порядок проверки правил важен: более специфичные категории идут раньше общих.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from config.settings import get_settings
from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import EventType
from geoanalytics.nlp._seqcls import SeqClsAdapter

log = get_logger("nlp.classify")

# Кортежи (категория, regex по корням слов). Регистр игнорируется.
_RULES: list[tuple[EventType, re.Pattern]] = [
    (EventType.SANCTIONS, re.compile(r"санкц|эмбарго|ограничени.{0,15}поставок|чёрн.{0,3}список", re.I)),
    (EventType.DIVIDENDS, re.compile(r"дивиденд|выплат.{0,10}акционер|отсечк", re.I)),
    (EventType.MERGER, re.compile(r"слияни|поглощени|приобрел.{0,5}долю|сделк.{0,5}M&A|выкуп.{0,10}акц", re.I)),
    (EventType.EARNINGS, re.compile(r"отчётност|отчетност|выручк|чист.{0,5}прибыл|чист.{0,5}убыт|EBITDA|финрезультат", re.I)),
    (EventType.REGULATION, re.compile(r"регулятор|законопроект|постановлени|ЦБ.{0,15}требовани|лицензи|налог", re.I)),
    (EventType.MACRO, re.compile(r"ключев.{0,5}ставк|инфляц|ВВП|курс.{0,5}рубл|нефт|бюджет|ставк.{0,5}ЦБ", re.I)),
    (EventType.GEOPOLITICS, re.compile(r"переговор|саммит|конфликт|дипломат|соглашени.{0,15}стран|внешн.{0,5}политик", re.I)),
]

# Маркеры шума (спорт/происшествия/культура/быт) — нерелевантно рынку. Проверяется ПОСЛЕ
# финансовых правил (если новость и про санкции, и про спорт — победит санкции), перед OTHER.
_NOISE_RE = re.compile(
    r"теннис|футбол|хоккей|баскетбол|\bматч\b|турнир|чемпионат|олимпиад|спортсмен|"
    r"\bдтп\b|\bавари[йяюе]|пожар|наводнени|землетрясени|ураган|"
    r"\bфильм|кино|концерт|\bактёр|\bактер|певец|певиц|\bсериал|шоу-бизнес|фестивал|"
    r"наркоман|ограблени",
    re.I,
)


def _label_to_event(label: str) -> EventType:
    """Строковая метка модели → EventType (неизвестная → OTHER)."""
    try:
        return EventType(label)
    except ValueError:
        return EventType.OTHER


def _classify_by_rules(text: str) -> EventType:
    for event_type, pattern in _RULES:
        if pattern.search(text):
            return event_type
    if _NOISE_RE.search(text):
        return EventType.NOISE
    return EventType.OTHER


@lru_cache
def _get_classifier() -> SeqClsAdapter | None:
    path = get_settings().event_adapter_path
    if not path:
        return None
    if not Path(path).exists():
        log.warning("event_adapter_missing", path=path)
        return None
    try:
        clf = SeqClsAdapter(path)
        log.info("event_classifier_ready", path=path)
        return clf
    except Exception as exc:  # noqa: BLE001 — модель опциональна, есть правиловый фолбэк
        log.warning("event_classifier_failed_rules", error=str(exc))
        return None


def model_status() -> tuple[str, str]:
    """Статус классификатора событий для health-check (I4): ("ok"|"degraded", деталь)."""
    configured = bool(get_settings().event_adapter_path)
    if _get_classifier() is not None:
        return "ok", "дообученная модель"
    if configured:
        return "degraded", "адаптер настроен, но не загрузился — активны ПРАВИЛА"
    return "ok", "правила (адаптер не настроен)"


def classify_event(text: str) -> EventType:
    """Определяет категорию события по тексту (заголовок + краткое описание).

    Дообученная модель (если настроена) → иначе правила по ключевым словам.
    """
    clf = _get_classifier()
    if clf is not None:
        try:
            return _label_to_event(clf.predict_label(text))
        except Exception as exc:  # noqa: BLE001
            log.warning("event_classify_failed_rules", error=str(exc))
    return _classify_by_rules(text)
