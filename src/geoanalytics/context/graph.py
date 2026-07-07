"""Граф факторов: что влияет на актив.

Для M2 факторы выводятся из:
- сектора актива и его пиров (другие активы того же сектора);
- явных связей в таблице relations (если заполнены);
- макро-индикаторов (общие для рынка РФ).

В M3 граф расширится событиями, странами и санкционными связями.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from geoanalytics.core.types import EntityType
from geoanalytics.storage.models import Asset, Relation, Sector


@dataclass
class AssetFactors:
    sector: str | None = None
    peers: list[str] = field(default_factory=list)             # тикеры-пиры
    macro_factors: list[str] = field(default_factory=list)     # ключевые макро-драйверы
    related: list[dict] = field(default_factory=list)          # явные связи из графа

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v}


# Разговорные алиасы секторов для entity-linking и резолва объекта из вопроса
# («как дела у нефтянки» → сектор «Нефть и газ»). Лемматизация ловит склонения.
_SECTOR_ALIASES: dict[str, list[str]] = {
    "Банки": ["банки", "банковский сектор", "банковская отрасль"],
    "Нефть и газ": ["нефтегаз", "нефтянка", "нефтегазовый сектор", "нефть и газ",
                    "нефтяной сектор", "нефтегазовая отрасль"],
    "Металлы и добыча": ["металлурги", "металлургия", "металлы и добыча",
                         "горнодобыча", "металлургический сектор"],
    "Потребительский сектор": ["ритейл", "потребительский сектор", "ритейлеры",
                               "потребительский рынок"],
    "Телеком": ["телеком", "телекоммуникации", "сектор связи"],
    "Технологии": ["технологии", "технологический сектор", "ИТ-сектор", "айти"],
    "Транспорт": ["транспорт", "транспортный сектор", "авиаперевозки"],
    "Финансы": ["финансовый сектор", "финансы"],
    "Химия и удобрения": ["химия", "удобрения", "химический сектор",
                          "производители удобрений"],
    "Электроэнергетика": ["электроэнергетика", "энергетический сектор",
                          "электроэнергетики", "генерация", "электросети"],
    "Девелопмент": ["девелоперы", "застройщики", "девелопмент",
                    "строительный сектор", "недвижимость"],
    "Фармацевтика": ["фарма", "фармацевтика", "фармацевтический сектор",
                     "фармкомпании", "производители лекарств", "здравоохранение"],
}


# Макро-драйверы по секторам (упрощённая экспертная карта; расширяется в M3).
_SECTOR_MACRO = {
    "Нефть и газ": ["цена нефти (Brent/Urals)", "курс USD/RUB", "санкции на экспорт"],
    "Банки": ["ключевая ставка ЦБ", "инфляция", "регуляторика ЦБ"],
    "Металлы и добыча": ["мировые цены на металлы", "курс USD/RUB", "экспортные пошлины"],
    "Потребительский сектор": ["инфляция", "реальные доходы населения", "ключевая ставка"],
    "Телеком": ["инфляция", "регуляторика", "капзатраты"],
    "Технологии": ["курс валют", "санкции на технологии", "регуляторика"],
    "Транспорт": ["цена топлива", "курс валют", "геополитика"],
    "Финансы": ["ключевая ставка ЦБ", "объёмы торгов", "регуляторика"],
    "Химия и удобрения": ["мировые цены на удобрения", "курс USD/RUB",
                          "экспортные ограничения"],
    "Электроэнергетика": ["тарифы", "ключевая ставка ЦБ", "капзатраты"],
    "Девелопмент": ["ключевая ставка ЦБ", "льготная ипотека", "цены на жильё"],
    "Фармацевтика": ["регуляторика Минздрава", "программа импортозамещения лекарств",
                     "курс валют (субстанции)"],
}


def assets_in_sector(session: Session, sector_id: int, exclude_id: int | None = None,
                     limit: int | None = None) -> list[Asset]:
    """Активы сектора (через company.sector_id). Переиспользуется factors и sector-анализом."""
    rows = session.scalars(select(Asset).join(Asset.company))
    out = [a for a in rows if a.company and a.company.sector_id == sector_id
           and a.id != exclude_id]
    return out[:limit] if limit else out


def sector_macro_factors(sector_name: str | None) -> list[str]:
    """Ключевые макро-драйверы сектора (экспертная карта, фолбэк — общие)."""
    return _SECTOR_MACRO.get(sector_name or "", ["ключевая ставка ЦБ", "курс валют"])


def factors_for_asset(session: Session, asset: Asset) -> AssetFactors:
    """Собирает факторы, влияющие на актив."""
    factors = AssetFactors()

    # Сектор и пиры (через компанию).
    sector_id = None
    if asset.company is not None and asset.company.sector_id is not None:
        sector_id = asset.company.sector_id
        sector = session.get(Sector, sector_id)
        factors.sector = sector.name if sector else None
        factors.macro_factors = sector_macro_factors(factors.sector)

    if sector_id is not None:
        factors.peers = [a.ticker for a in
                         assets_in_sector(session, sector_id, exclude_id=asset.id, limit=10)]

    # Явные связи из графа (если заполнены).
    rels = session.scalars(
        select(Relation).where(
            Relation.subject_type == EntityType.ASSET.value,
            Relation.subject_id == asset.id,
        )
    )
    for r in rels:
        factors.related.append({"predicate": r.predicate,
                                "object_type": r.object_type, "object_id": r.object_id})

    return factors
