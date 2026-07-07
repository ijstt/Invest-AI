"""Извлечение именованных сущностей (NER) из русских текстов через Natasha.

Natasha — лёгкий CPU-only стек (без torch), отлично подходит под ограничения железа.
Возвращаем нормализованные упоминания ORG/PER/LOC. Если Natasha недоступна —
пустой список (graceful degradation).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from geoanalytics.core.logging import get_logger

log = get_logger("nlp.ner")

# Маппинг типов Natasha → наши категории сущностей.
_TYPE_MAP = {"ORG": "ORG", "PER": "PER", "LOC": "LOC"}


@dataclass(slots=True)
class Mention:
    """Упоминание сущности в тексте."""

    text: str            # как написано в тексте
    normal: str          # нормализованная форма (для матчинга)
    type: str            # ORG | PER | LOC


class _NatashaNer:
    """Ленивая обёртка над пайплайном Natasha."""

    def __init__(self) -> None:
        from natasha import (
            Doc,
            MorphVocab,
            NewsEmbedding,
            NewsMorphTagger,
            NewsNERTagger,
            Segmenter,
        )

        self._Doc = Doc
        self._segmenter = Segmenter()
        self._morph_vocab = MorphVocab()
        emb = NewsEmbedding()
        self._morph_tagger = NewsMorphTagger(emb)
        self._ner_tagger = NewsNERTagger(emb)

    def extract(self, text: str) -> list[Mention]:
        doc = self._Doc(text)
        doc.segment(self._segmenter)
        doc.tag_morph(self._morph_tagger)
        doc.tag_ner(self._ner_tagger)
        mentions: list[Mention] = []
        for span in doc.spans:
            if span.type not in _TYPE_MAP:
                continue
            span.normalize(self._morph_vocab)
            mentions.append(
                Mention(text=span.text, normal=span.normal or span.text, type=span.type)
            )
        return mentions

    def lemmatize(self, text: str) -> list[str]:
        """Леммы токенов текста (нижний регистр, ё→е). Без NER-тэггинга — быстрее."""
        doc = self._Doc(text)
        doc.segment(self._segmenter)
        doc.tag_morph(self._morph_tagger)
        out: list[str] = []
        for tok in doc.tokens:
            tok.lemmatize(self._morph_vocab)
            out.append((tok.lemma or tok.text).lower().replace("ё", "е"))
        return out


@lru_cache
def _get_ner() -> _NatashaNer | None:
    try:
        ner = _NatashaNer()
        log.info("ner_ready", backend="natasha")
        return ner
    except Exception as exc:  # noqa: BLE001 — NER опционален
        log.warning("ner_unavailable", error=str(exc))
        return None


def model_status() -> tuple[str, str]:
    """Статус NER для health-check (I4): без NER не появляются связи статья↔актив."""
    if _get_ner() is None:
        return "degraded", "Natasha не загрузилась — линковка сущностей отключена"
    return "ok", "natasha"


def extract_entities(text: str) -> list[Mention]:
    """Извлекает сущности из текста. Пустой список, если NER недоступен."""
    ner = _get_ner()
    if ner is None:
        return []
    try:
        return ner.extract(text)
    except Exception as exc:  # noqa: BLE001
        log.warning("ner_failed", error=str(exc))
        return []


def lemmas(text: str) -> list[str] | None:
    """Леммы токенов текста для морфологического матчинга (entity-linking).

    None — если морфология недоступна (тогда матчер использует свой простой токенайзер).
    """
    ner = _get_ner()
    if ner is None:
        return None
    try:
        return ner.lemmatize(text)
    except Exception as exc:  # noqa: BLE001
        log.warning("lemmatize_failed", error=str(exc))
        return None
