"""Связывание упоминаний из новостей с активами/компаниями (entity linking).

Строит индекс алиасов из БД (компании + их активы) и сопоставляет его с текстом
новости и извлечёнными NER-упоминаниями. Это даёт ответ «какие новости относятся
к активу X»: создаются связи article↔entity на уровне и компании, и её активов.

Матчинг — по **леммам** (M6): и текст, и алиасы приводятся к начальной форме через
Natasha, после чего ищется совпадение фразы лемм с границами слова. Это разом решает
две проблемы M1-матчера: склонённые формы («Сбербанка», «МосБирже», «Газпрому») теперь
матчатся, а ложные подстроки («газ» внутри «Газпром») — нет (токен ≠ подстрока). Если
морфология недоступна, матчер деградирует к простому токенному совпадению (graceful).

Помимо самой связи матчер оценивает `relevance` (уверенность привязки): точный тикер —
1.0; многословный алиас — выше однословного; подтверждение NER-упоминанием ORG — буст.
Релевантность идёт в `ArticleEntity.relevance` и далее в значимость новости и силу
влияния события.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from geoanalytics.core.types import EntityType
from geoanalytics.nlp import ner
from geoanalytics.storage.models import Asset, Company

_TOKEN_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)


@dataclass(slots=True)
class EntityLink:
    """Найденная связь статьи с сущностью."""

    entity_type: EntityType
    entity_id: int
    mention: str
    relevance: float = 1.0


def _normalize(text: str) -> str:
    return text.lower().replace("ё", "е")


def _fallback_tokens(text: str) -> list[str]:
    """Токены без морфологии (когда Natasha недоступна)."""
    return [_normalize(t) for t in _TOKEN_RE.findall(text)]


def _lemma_tokens(text: str) -> list[str]:
    """Леммы значимых токенов текста; фолбэк — простые токены."""
    out = ner.lemmas(text)
    if out is None:
        return _fallback_tokens(text)
    return [t for t in out if _TOKEN_RE.fullmatch(t)]


def _alias_relevance(lemma_phrase: str) -> float:
    """Базовая уверенность привязки по алиасу: многословный — надёжнее однословного."""
    return 0.9 if len(lemma_phrase.split()) >= 2 else 0.7


def _capitalized_in(text: str, alias: str) -> bool:
    """Есть ли в тексте ЗАГЛАВНАЯ форма однословного кириллического алиаса.

    «Полюс»/«Магнит» — обычные существительные: строчная форма в тексте — не
    компания («Национально-демократический полюс» ложно линковал новость про
    Армению на PLZL; «северный полюс» — туда же). Имя компании в новости всегда
    с заглавной или капсом, склонения покрыты суффиксом. Для некириллических
    алиасов проверка не нужна (тикеры матчатся отдельной веткой, точно).
    """
    first = alias[0]
    if not re.match(r"[а-яё]", first):
        return True
    # Основа без конечной гласной/«ь»: при склонении она меняется («Мосбиржа» →
    # «на Мосбирже», «Северсталь» → «у Северстали») и полный алиас не совпал бы.
    stem = alias.rstrip("аеёиоуыэюяь")
    if len(stem) < 3:  # совсем короткая основа — оставляем полный алиас
        stem = alias
    pattern = rf"\b{first.upper()}(?i:{re.escape(stem[1:])})[а-яёА-ЯЁ]*"
    return re.search(pattern, text) is not None


class EntityIndex:
    """Индекс алиасов → целевые сущности. Строится один раз на батч обработки."""

    def __init__(self, session: Session) -> None:
        # alias(normal) → список целей (тип, id)
        self._alias_targets: dict[str, list[tuple[EntityType, int]]] = {}
        # для матчинга тикеров как отдельных слов (SBER, GAZP)
        self._ticker_targets: dict[str, list[tuple[EntityType, int]]] = {}
        self._build(session)
        self._reindex_lemmas()

    def _add_alias(self, alias: str, target: tuple[EntityType, int]) -> None:
        key = _normalize(alias)
        if len(key) < 3:  # слишком короткие алиасы дают ложные срабатывания
            return
        self._alias_targets.setdefault(key, []).append(target)

    def _build(self, session: Session) -> None:
        # Активы: тикер (точное слово) и название.
        assets = list(session.scalars(select(Asset)))
        company_assets: dict[int, list[int]] = {}
        for a in assets:
            self._ticker_targets.setdefault(a.ticker.lower(), []).append(
                (EntityType.ASSET, a.id)
            )
            self._add_alias(a.name, (EntityType.ASSET, a.id))
            if a.company_id:
                company_assets.setdefault(a.company_id, []).append(a.id)

        # Компании: имя + алиасы. Линкуем и на компанию, и на её активы.
        for c in session.scalars(select(Company)):
            targets: list[tuple[EntityType, int]] = [(EntityType.COMPANY, c.id)]
            targets += [(EntityType.ASSET, aid) for aid in company_assets.get(c.id, [])]
            names = [c.name, *(c.aliases or [])]
            for name in names:
                for t in targets:
                    self._add_alias(name, t)

        # Секторы и страны (M+): индексируем по названию + разговорным алиасам, чтобы
        # связывать новости и резолвить объект вопроса не только по тикерам. Локальный
        # импорт — карты алиасов живут в graph/seed, избегаем циклов на уровне модуля.
        from geoanalytics.context.graph import _SECTOR_ALIASES
        from geoanalytics.storage.models import Country, Sector
        from geoanalytics.storage.seed import COUNTRIES

        for sec in session.scalars(select(Sector)):
            self._add_alias(sec.name, (EntityType.SECTOR, sec.id))
            for al in _SECTOR_ALIASES.get(sec.name, []):
                self._add_alias(al, (EntityType.SECTOR, sec.id))

        for country in session.scalars(select(Country)):
            self._add_alias(country.name, (EntityType.COUNTRY, country.id))
            for al in COUNTRIES.get(country.code, ("", []))[1]:
                self._add_alias(al, (EntityType.COUNTRY, country.id))

    def _reindex_lemmas(self) -> None:
        """Лемма-форма каждого алиаса (один раз на индекс) для фраза-матчинга."""
        self._alias_lemma: dict[str, str] = {
            alias: " ".join(_lemma_tokens(alias)) for alias in self._alias_targets
        }

    def match(self, text: str, ner_mentions: list[str] | None = None) -> list[EntityLink]:
        """Находит сущности в тексте. Дедуплицирует по (type, id), оценивает relevance."""
        if not hasattr(self, "_alias_lemma"):  # индекс мог быть собран в обход __init__
            self._reindex_lemmas()
        norm = _normalize(text)
        lemma_str = f" {' '.join(_lemma_tokens(text))} "
        # target → {mention, relevance}
        found: dict[tuple[EntityType, int], dict] = {}

        def record(target: tuple[EntityType, int], mention: str, rel: float) -> None:
            cur = found.get(target)
            if cur is None or rel > cur["relevance"]:
                found[target] = {"mention": mention, "relevance": rel}

        def _needs_cap_check(phrase: str, targets: list[tuple[EntityType, int]]) -> bool:
            """Гейт заглавной буквы — только однословные алиасы активов/компаний.

            Секторов/стран не касается: их алиасы («банки», «нефтянка») в тексте
            легитимно строчные."""
            return " " not in phrase and any(
                t[0] in (EntityType.ASSET, EntityType.COMPANY) for t in targets
            )

        # 1) алиасы по фразе лемм (границы слова обеспечены пробелами вокруг фразы)
        for alias, targets in self._alias_targets.items():
            phrase = self._alias_lemma.get(alias) or alias
            if f" {phrase} " in lemma_str:
                if _needs_cap_check(phrase, targets) and not _capitalized_in(text, alias):
                    continue
                rel = _alias_relevance(phrase)
                for t in targets:
                    record(t, alias, rel)

        # 2) тикеры как отдельные слова (латиница, морфология не нужна)
        for ticker, targets in self._ticker_targets.items():
            if re.search(rf"\b{re.escape(ticker)}\b", norm):
                for t in targets:
                    record(t, ticker.upper(), 1.0)

        # 3) NER-упоминания ORG усиливают матчинг по алиасам (фраза лемм mention ↔ alias).
        # Совпадение — ТОЛЬКО по границам слов в обе стороны: алиас как фраза внутри
        # упоминания ИЛИ упоминание как фраза внутри алиаса. Без границ короткое
        # упоминание-аббревиатура ловилось как подстрока («ЕС»/«Си»/«МО» внутри
        # «мобильный телесистема») и ложно линковало новость на MTSS — баг подстрочного
        # матча, которого фраза-леммы как раз должна избегать.
        for mention in ner_mentions or []:
            m_phrase = f" {' '.join(_lemma_tokens(mention))} "
            ms = m_phrase.strip()
            if not ms:
                continue
            for alias, targets in self._alias_targets.items():
                phrase = self._alias_lemma.get(alias) or alias
                if not phrase:
                    continue
                if f" {phrase} " in m_phrase or f" {ms} " in f" {phrase} ":
                    # Тот же гейт заглавной буквы, но по ПОВЕРХНОСТИ упоминания:
                    # «Национально-демократический полюс» (строчный «полюс») ≠ ПАО «Полюс».
                    if (_needs_cap_check(phrase, targets)
                            and not _capitalized_in(mention, alias)):
                        continue
                    rel = min(1.0, _alias_relevance(phrase) + 0.2)
                    for t in targets:
                        record(t, mention, rel)

        return [
            EntityLink(etype, eid, info["mention"], round(info["relevance"], 3))
            for (etype, eid), info in found.items()
        ]
