"""Общие доменные типы и перечисления, используемые в разных слоях."""

from __future__ import annotations

from enum import StrEnum


class SourceKind(StrEnum):
    """Тип источника данных."""

    NEWS = "news"          # новостные ленты (Интерфакс и т.п.)
    MARKET = "market"      # котировки/индексы (МосБиржа)
    MACRO = "macro"        # макропоказатели (ЦБ РФ: ставка, курсы)


class EntityType(StrEnum):
    """Тип сущности в графе знаний."""

    ASSET = "asset"        # торгуемый инструмент (тикер)
    COMPANY = "company"
    SECTOR = "sector"
    COUNTRY = "country"
    PERSON = "person"
    EVENT = "event"
    MACRO_THEME = "macro_theme"   # макро-тема (санкции, инфляция, ставка, курс…)


class Sentiment(StrEnum):
    """Метка тональности."""

    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class EventType(StrEnum):
    """Категория новостного события (для классификатора)."""

    SANCTIONS = "sanctions"
    DIVIDENDS = "dividends"
    MERGER = "merger"          # M&A
    REGULATION = "regulation"
    EARNINGS = "earnings"      # отчётность
    MACRO = "macro"
    GEOPOLITICS = "geopolitics"
    OTHER = "other"
    NOISE = "noise"            # спорт/происшествия/культура — нерелевантный рынку шум
