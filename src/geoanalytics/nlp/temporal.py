"""F3 (Волна 3): temporal anchoring — дата события ≠ дата публикации.

Две части:
1) классификатор временно́го статуса (past/future/forecast/none) — дообученный
   tiny2 (золото Qwen, scripts/llm_label_temporal.py), graceful fallback None;
2) rule-based извлечение даты события из текста: явные даты («10 июля 2026»,
   «15.06.2026») и относительные слова («вчера», «завтра»), заякоренные на дату
   публикации. Выбор даты согласован со статусом: future → ближайшая будущая,
   past → последняя прошедшая.

Зачем: event study (E1) должен мерить реакцию от даты СОБЫТИЯ; «дивиденды
будут» и «отсечка прошла» — разная торговая ценность для алертов и сводки.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from config.settings import get_settings
from geoanalytics.core.logging import get_logger
from geoanalytics.nlp._seqcls import ModelConfig, ModelLoader

log = get_logger("nlp.temporal")

PAST, FUTURE, FORECAST, NONE = "past", "future", "forecast", "none"
LABELS = (PAST, FUTURE, FORECAST, NONE)

_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11,
    "декабря": 12,
}
# «10 июля», «10 июля 2026 [года]» — \s включает nbsp.
_RU_DATE_RE = re.compile(
    r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")(?:\s+(\d{4}))?", re.IGNORECASE
)
# «15.06.2026», «15.06.26» — на границах слов, чтобы не ловить версии/суммы.
_NUM_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})(?!\d)")
# Относительные слова → смещение в днях от даты публикации.
_RELATIVE = {
    "позавчера": -2, "вчера": -1, "сегодня": 0, "завтра": 1, "послезавтра": 2,
}
_RELATIVE_RE = re.compile(r"\b(" + "|".join(_RELATIVE) + r")\b", re.IGNORECASE)

# Дальше этого окна даты считаем мусором распознавания (опечатки, история).
_MAX_SPAN_DAYS = 400


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def extract_event_dates(text: str, published: date) -> list[date]:
    """Все упомянутые в тексте даты, заякоренные на дату публикации.

    Явные даты без года получают год, дающий дату БЛИЖЕ к публикации
    (декабрьская новость про «15 января» — это январь следующего года).
    Даты дальше ±_MAX_SPAN_DAYS от публикации отбрасываются. Порядок —
    по появлению в тексте, без дублей.
    """
    out: list[date] = []

    def add(d: date | None) -> None:
        if d is not None and abs((d - published).days) <= _MAX_SPAN_DAYS and d not in out:
            out.append(d)

    for m in _RU_DATE_RE.finditer(text):
        day, month = int(m.group(1)), _MONTHS[m.group(2).lower()]
        if m.group(3):
            add(_safe_date(int(m.group(3)), month, day))
        else:
            candidates = [_safe_date(published.year + dy, month, day)
                          for dy in (-1, 0, 1)]
            best = min((c for c in candidates if c is not None),
                       key=lambda c: abs((c - published).days), default=None)
            add(best)

    for m in _NUM_DATE_RE.finditer(text):
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        add(_safe_date(year, month, day))

    for m in _RELATIVE_RE.finditer(text):
        add(published + timedelta(days=_RELATIVE[m.group(1).lower()]))

    return out


def anchor_event_date(dates: list[date], published: date,
                      status: str) -> date | None:
    """Дата события, согласованная с временны́м статусом новости.

    future → ближайшая дата ПОСЛЕ публикации; past → последняя дата НЕ ПОЗЖЕ
    публикации; forecast/none → None (события с датой нет). Если подходящей
    даты в тексте не нашлось — None (событие = день публикации по умолчанию,
    решает потребитель).
    """
    if status == FUTURE:
        future_dates = [d for d in dates if d > published]
        return min(future_dates) if future_dates else None
    if status == PAST:
        past_dates = [d for d in dates if d <= published]
        return max(past_dates) if past_dates else None
    return None


_CFG = ModelConfig(
    name="temporal",
    err_level="error",
    missing_key="temporal_adapter_missing_FALLBACK",
    ready_key="temporal_model_ready",
    failed_key="temporal_model_load_failed_FALLBACK",
    loaded_desc="temporal: модель",
    fallback_desc="temporal: НЕ ЗАГРУЗИЛСЯ (статус/дата события NULL)",
    unconfigured_desc="temporal: не настроен (статус/дата события NULL)",
)

_LOADER = ModelLoader(_CFG, lambda: get_settings().temporal_adapter_path, log)


def _model():
    """SeqClsAdapter temporal-классификатора; None — фолбэк (статус неизвестен)."""
    return _LOADER.get_model()


def classify_temporal(text: str) -> str | None:
    clf = _model()
    if clf is not None:
        try:
            return clf.predict_label(text)
        except Exception as exc:  # noqa: BLE001
            log.error("temporal_predict_failed_FALLBACK", error=str(exc))
    return None


def model_status() -> tuple[str, str]:
    """Статус F3 для health-check: degraded, если настроено, но не загрузилось."""
    return _LOADER.get_status()


def temporal_anchor(text: str, published: date) -> tuple[str | None, date | None]:
    """Статус + дата события одной операцией (для processing/rescore)."""
    status = classify_temporal(text)
    if status is None:
        return None, None
    return status, anchor_event_date(extract_event_dates(text, published),
                                     published, status)
