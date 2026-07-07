"""Утилиты очистки текста перед анализом."""

from __future__ import annotations

import html
import re

from selectolax.parser import HTMLParser

_WS_RE = re.compile(r"\s+")
_WS_PUNCT_RE = re.compile(r"[^\w]+", flags=re.UNICODE)


def clean_text(value: str | None) -> str:
    """Убирает HTML-теги и нормализует пробелы."""
    if not value:
        return ""
    text = value
    if "<" in value and ">" in value:  # похоже на HTML
        text = HTMLParser(value).text(separator=" ")
    return _WS_RE.sub(" ", text).strip()


def normalized_text(value: str | None) -> str:
    """Текст без косметических различий: HTML-сущности раскрыты, регистр и пунктуация/
    пробелы схлопнуты. Лечит near-дубли вида «атаки&nbsp;БПЛА» vs «атаки БПЛА» (одна
    новость от разных лент/перепостов). Чистая функция — единый источник нормализации
    для дедупа raw-слоя (`storage.repositories.normalized_hash`) и обучающего золота
    (`nlp.dataset.dedup_normalized`)."""
    return _WS_PUNCT_RE.sub(" ", html.unescape(value or "").lower()).strip()
