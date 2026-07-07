"""Тесты матчинга сущностей (без БД — индекс наполняется вручную)."""

from __future__ import annotations

import pytest

from geoanalytics.core.types import EntityType
from geoanalytics.nlp import ner
from geoanalytics.nlp.entity_linking import EntityIndex

# Лемматизация (склонения) требует доступной морфологии Natasha. Если её нет —
# матчер деградирует к токенному совпадению, и тесты на падежи неактуальны.
_MORPH_OK = ner.lemmas("Сбербанка") not in (None, ["сбербанка"])


def _index() -> EntityIndex:
    """Создаёт индекс в обход __init__ (без обращения к БД)."""
    idx = EntityIndex.__new__(EntityIndex)
    idx._alias_targets = {
        "сбербанк": [(EntityType.COMPANY, 1), (EntityType.ASSET, 10)],
        "сбер": [(EntityType.COMPANY, 1), (EntityType.ASSET, 10)],
        "газпром": [(EntityType.COMPANY, 2), (EntityType.ASSET, 20)],
    }
    idx._ticker_targets = {
        "sber": [(EntityType.ASSET, 10)],
        "gazp": [(EntityType.ASSET, 20)],
    }
    return idx


def test_match_by_alias():
    links = _index().match("Сбербанк отчитался о прибыли")
    targets = {(lk.entity_type, lk.entity_id) for lk in links}
    assert (EntityType.COMPANY, 1) in targets
    assert (EntityType.ASSET, 10) in targets


def test_match_by_ticker_word_boundary():
    links = _index().match("Акции SBER выросли")
    assert (EntityType.ASSET, 10) in {(lk.entity_type, lk.entity_id) for lk in links}


def test_no_false_substring_ticker():
    # 'gazp' не должен матчиться внутри другого слова
    links = _index().match("слово gazprombank не тикер")
    assert (EntityType.ASSET, 20) not in {(lk.entity_type, lk.entity_id) for lk in links}


def test_ner_mention_no_false_substring():
    # Регресс: короткое NER-упоминание-аббревиатура не должно линковаться как ПОДСТРОКА
    # многословного алиаса. «ЕС»/«Си»/«МО» ловились внутри «мобильный телесистема» и
    # ложно привязывали новости к MTSS. Матч NER↔алиас — только по границам слов.
    idx = EntityIndex.__new__(EntityIndex)
    idx._alias_targets = {"мобильные телесистемы": [(EntityType.ASSET, 30)]}
    idx._ticker_targets = {}
    links = idx.match("ЕС ввел санкции против Ирана", ner_mentions=["ЕС", "Си", "МО"])
    assert (EntityType.ASSET, 30) not in {(lk.entity_type, lk.entity_id) for lk in links}
    # Полноценное упоминание компании по-прежнему линкуется.
    links2 = idx.match("Мобильные ТелеСистемы отчитались", ner_mentions=["Мобильные ТелеСистемы"])
    assert (EntityType.ASSET, 30) in {(lk.entity_type, lk.entity_id) for lk in links2}


def test_match_dedup():
    # 'Сбербанк' и 'Сбер' оба укажут на те же сущности — без дублей.
    links = _index().match("Сбербанк и Сбер — одно и то же")
    keys = [(lk.entity_type, lk.entity_id) for lk in links]
    assert len(keys) == len(set(keys))


def test_ё_normalization():
    idx = EntityIndex.__new__(EntityIndex)
    idx._alias_targets = {"полюс": [(EntityType.COMPANY, 3)]}
    idx._ticker_targets = {}
    links = idx.match("Полюс нарастил добычу")
    assert (EntityType.COMPANY, 3) in {(lk.entity_type, lk.entity_id) for lk in links}


@pytest.mark.skipif(not _MORPH_OK, reason="морфология (Natasha) недоступна")
def test_match_declined_forms():
    # M6: склонённые формы должны матчиться по леммам (раньше терялись).
    links = _index().match("Акции Сбербанка и Газпрома подорожали")
    targets = {(lk.entity_type, lk.entity_id) for lk in links}
    assert (EntityType.ASSET, 10) in targets   # Сбербанка → сбербанк
    assert (EntityType.ASSET, 20) in targets   # Газпрома → газпром


def test_relevance_ticker_is_max():
    links = _index().match("Акции SBER выросли")
    sber = next(lk for lk in links if (lk.entity_type, lk.entity_id) == (EntityType.ASSET, 10))
    assert sber.relevance == 1.0


def test_no_false_substring_alias():
    # 'газ' как часть другого слова не должен давать ложную привязку к Газпрому.
    links = _index().match("Магазин открылся в городе")
    assert (EntityType.ASSET, 20) not in {(lk.entity_type, lk.entity_id) for lk in links}


def _polus_index() -> EntityIndex:
    idx = EntityIndex.__new__(EntityIndex)
    idx._alias_targets = {"полюс": [(EntityType.COMPANY, 5), (EntityType.ASSET, 50)]}
    idx._ticker_targets = {"plzl": [(EntityType.ASSET, 50)]}
    return idx


def test_lowercase_common_noun_alias_not_linked():
    # Регресс: «Национально-демократический полюс» (партия в Армении, строчный «полюс»)
    # ложно линковался на PLZL. Однословный кириллический алиас актива/компании требует
    # ЗАГЛАВНОЙ формы в тексте — имя компании в новости всегда с заглавной.
    idx = _polus_index()
    text = "В Армении оппозиция и Национально-демократический полюс потребовали пересчёта"
    links = idx.match(text, ner_mentions=["Национально-демократический полюс"])
    assert (EntityType.ASSET, 50) not in {(lk.entity_type, lk.entity_id) for lk in links}


def test_lowercase_geographic_noun_not_linked():
    idx = _polus_index()
    links = idx.match("Экспедиция достигла северного полюса")
    assert (EntityType.ASSET, 50) not in {(lk.entity_type, lk.entity_id) for lk in links}


def test_capitalized_company_alias_still_linked():
    idx = _polus_index()
    links = idx.match("«Полюс» нарастил добычу золота", ner_mentions=["Полюс"])
    assert (EntityType.ASSET, 50) in {(lk.entity_type, lk.entity_id) for lk in links}


@pytest.mark.skipif(not _MORPH_OK, reason="морфология Natasha недоступна")
def test_capitalized_declined_company_alias_linked():
    idx = _polus_index()
    links = idx.match("Акции Полюса выросли на отчётности")
    assert (EntityType.ASSET, 50) in {(lk.entity_type, lk.entity_id) for lk in links}


def test_allcaps_company_alias_linked():
    idx = _polus_index()
    links = idx.match("ПОЛЮС УВЕЛИЧИЛ ДИВИДЕНДЫ")
    assert (EntityType.ASSET, 50) in {(lk.entity_type, lk.entity_id) for lk in links}


@pytest.mark.skipif(not _MORPH_OK, reason="морфология Natasha недоступна")
def test_capitalized_alias_with_changed_ending_linked():
    # «Мосбиржа» → «на Мосбирже»: при склонении меняется конечная гласная алиаса —
    # проверка заглавной формы должна матчить по ОСНОВЕ, не по полному алиасу.
    idx = EntityIndex.__new__(EntityIndex)
    idx._alias_targets = {"мосбиржа": [(EntityType.ASSET, 17)]}
    idx._ticker_targets = {}
    links = idx.match("Акции компании выросли на Мосбирже", ner_mentions=["Мосбирже"])
    assert (EntityType.ASSET, 17) in {(lk.entity_type, lk.entity_id) for lk in links}
