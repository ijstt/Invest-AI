"""Стартовый справочник крупных эмитентов РФ для entity-linking.

Заполняет sectors / companies (с алиасами) / assets. Идемпотентно: повторный
запуск не плодит дубликаты (поиск по уникальным ключам). Активы дополнительно
обогащаются справочными данными при ингесте MOEX, но базовые алиасы нужны, чтобы
связывать новости ещё до первого рыночного среза.
"""

from __future__ import annotations

from sqlalchemy import select

from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import EntityType
from geoanalytics.nlp.themes import THEME_KEYWORDS
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import (
    Asset,
    Company,
    Country,
    MacroTheme,
    Relation,
    Sector,
)

log = get_logger("seed")

# Страны для анализа экономики/геополитики + разговорные алиасы (резолв из вопроса
# и связывание новостей). code(ISO) → (название, [алиасы]).
COUNTRIES: dict[str, tuple[str, list[str]]] = {
    "RUS": ("Россия", ["Россия", "РФ", "российский", "Российская Федерация"]),
    "USA": ("США", ["США", "Америка", "Соединённые Штаты", "американский", "штаты"]),
    "EU": ("Евросоюз", ["Евросоюз", "ЕС", "Европа", "еврозона", "европейский"]),
    "CN": ("Китай", ["Китай", "КНР", "китайский", "Пекин"]),
}

# ticker → (название, сектор, [алиасы для новостей])
ISSUERS: dict[str, tuple[str, str, list[str]]] = {
    "SBER": ("Сбербанк", "Банки", ["Сбербанк", "Сбер", "Sberbank"]),
    "VTBR": ("Банк ВТБ", "Банки", ["ВТБ", "VTB"]),
    "GAZP": ("Газпром", "Нефть и газ", ["Газпром", "Gazprom"]),
    "ROSN": ("Роснефть", "Нефть и газ", ["Роснефть", "Rosneft"]),
    "LKOH": ("Лукойл", "Нефть и газ", ["Лукойл", "Lukoil"]),
    "NVTK": ("Новатэк", "Нефть и газ", ["Новатэк", "Novatek"]),
    "TATN": ("Татнефть", "Нефть и газ", ["Татнефть", "Tatneft"]),
    "SNGS": ("Сургутнефтегаз", "Нефть и газ", ["Сургутнефтегаз", "Сургут"]),
    "GMKN": ("Норникель", "Металлы и добыча",
             ["Норникель", "Норильский никель", "ГМК", "Nornickel"]),
    "PLZL": ("Полюс", "Металлы и добыча", ["Полюс", "Polyus"]),
    "CHMF": ("Северсталь", "Металлы и добыча", ["Северсталь", "Severstal"]),
    "ALRS": ("Алроса", "Металлы и добыча", ["Алроса", "Alrosa"]),
    "MGNT": ("Магнит", "Потребительский сектор", ["Магнит", "Magnit"]),
    "MTSS": ("МТС", "Телеком", ["МТС", "Мобильные ТелеСистемы", "MTS"]),
    "YDEX": ("Яндекс", "Технологии", ["Яндекс", "Yandex"]),
    "AFLT": ("Аэрофлот", "Транспорт", ["Аэрофлот", "Aeroflot"]),
    "MOEX": ("Московская биржа", "Финансы", ["Московская биржа", "Мосбиржа", "MOEX"]),
    # --- Второй эшелон + недостающие фишки (расширение 2026-06-13, tickers.txt). ---
    # Алиасы выбираются с оглядкой на матчер: однословные кириллические проходят
    # гейт заглавной буквы (entity_linking._capitalized_in), но омонимы частых слов
    # («ПИК» — пик инфляции, «Система» — система платежей, «Самолёт» — авиация)
    # даём только в составных формах.
    "TRNFP": ("Транснефть", "Нефть и газ", ["Транснефть", "Transneft"]),
    "NLMK": ("НЛМК", "Металлы и добыча", ["НЛМК", "Новолипецкий металлургический"]),
    "MAGN": ("ММК", "Металлы и добыча", ["ММК", "Магнитогорский металлургический"]),
    "RUAL": ("Русал", "Металлы и добыча", ["Русал", "Rusal"]),
    "PHOR": ("ФосАгро", "Химия и удобрения", ["ФосАгро", "Фосагро", "PhosAgro"]),
    "AKRN": ("Акрон", "Химия и удобрения", ["Акрон", "Acron"]),
    "IRAO": ("Интер РАО", "Электроэнергетика", ["Интер РАО", "Интер РАО ЕЭС"]),
    "HYDR": ("РусГидро", "Электроэнергетика", ["РусГидро", "Русгидро"]),
    "FEES": ("Россети", "Электроэнергетика", ["Россети", "ФСК ЕЭС", "ФСК-Россети"]),
    "UPRO": ("Юнипро", "Электроэнергетика", ["Юнипро", "Unipro"]),
    "PIKK": ("Группа ПИК", "Девелопмент", ["Группа ПИК", "ПИК СЗ", "ГК ПИК"]),
    "SMLT": ("ГК Самолёт", "Девелопмент",
             ["ГК Самолёт", "Группа Самолёт", "ГК «Самолет»"]),
    "FLOT": ("Совкомфлот", "Транспорт", ["Совкомфлот", "Sovcomflot"]),
    "RTKM": ("Ростелеком", "Телеком", ["Ростелеком", "Rostelecom"]),
    "OZON": ("Озон", "Технологии", ["Озон", "Ozon"]),
    "VKCO": ("VK", "Технологии", ["ВКонтакте", "VK Company", "холдинг VK"]),
    "POSI": ("Группа Позитив", "Технологии",
             ["Группа Позитив", "Positive Technologies", "Позитив Текнолоджиз"]),
    "ASTR": ("Группа Астра", "Технологии", ["Группа Астра", "ГК Астра"]),
    "BSPB": ("Банк Санкт-Петербург", "Банки", ["Банк Санкт-Петербург", "БСПБ"]),
    "T": ("Т-Технологии", "Банки", ["Т-Технологии", "TCS", "Тинькофф", "ТКС", "ТКС Холдинг"]),
    "PRMD": ("Промомед", "Фармацевтика", ["Промомед", "Promomed", "ПРОМОМЕД"]),
    # АФК Система — диверсифицированный холдинг (L2: владеет MTSS, OZON и др.). «Система» —
    # частый омоним, поэтому только составные алиасы.
    "AFKS": ("АФК Система", "Финансы", ["АФК Система", "АФК «Система»", "Sistema"]),
}

# Фонды денежного рынка (БПИФ на MOEX, доска TQTF): «квазикэш» — равномерный рост NAV, почти
# нулевая волатильность. Добавляются как Asset kind="fund" (история свечей грузится тем же
# `geo backfill` — рыночный URL `markets/shares/.../candles` board-агностичен). Сектор
# «Денежный рынок» — отдельная группа в составе/графе. Алиасы пустые: фонды НЕ субъекты новостей,
# generic-имена («Ликвидность») дали бы ложные срабатывания линковки.
MONEY_MARKET_FUNDS: dict[str, str] = {
    "LQDT": "Ликвидность (ВИМ Инвестиции)",
    "AKMM": "Альфа-Капитал Денежный рынок",
    "SBMM": "Сбер — Денежный рынок",
    "TMON": "Т-Капитал Денежный рынок",
}
MMF_SECTOR = "Денежный рынок"

# C2: курируемая горстка ликвидных фьючерсов FORTS (Asset kind="future", доска RFUD, сектор
# «Срочный рынок»). Значение — (имя, ISS asset_code): бэкфилл по asset_code находит фронтальный
# (ближайший по экспирации) контракт и грузит его свечи (`history._front_futures_secid`).
# Авто-склейки экспираций (price-adjust на роллах) сознательно нет — на роллах ряд имеет стык.
# Алиасы пустые (фьючерсы — не субъекты новостей). Без портфеля/ГО по фьючам (вне скоупа).
FUTURES: dict[str, tuple[str, str]] = {
    "BR": ("Brent (фьючерс)", "BR"),
    "GD": ("Золото (фьючерс)", "GOLD"),
    "SI": ("USD/RUB (фьючерс)", "Si"),
    "EU": ("EUR/RUB (фьючерс)", "Eu"),
    "CNY": ("CNY/RUB (фьючерс)", "CNY"),
    "RTS": ("Индекс РТС (фьючерс)", "RTS"),
}
FUTURES_SECTOR = "Срочный рынок"

# Бенчмарк-индекс для расчёта alpha в бэктестах (B4). Это НЕ торгуемый эмитент:
# индекс МосБиржи, kind="index", без компании и алиасов (чтобы не мешать линковке
# новостей). История грузится тем же `geo backfill` через индексный рынок ISS.
BENCHMARK_TICKER = "IMOEX"
BENCHMARK_NAME = "Индекс МосБиржи"


# --------------------------------------------------------------------------- #
# G7 граф знаний: межэмитентные рёбра (supply-chain + конкуренты). Курируется
# вручную по доменному знанию рынка РФ; веса = сила связи. Распространение влияния
# (analytics/graph_impact.py) аттенюирует событие соседа по весу ребра.
# --------------------------------------------------------------------------- #

# supplier_of: (поставщик, потребитель, вес). Шок спроса/предложения СО-движет
# поставщика и потребителя в одну сторону (затухая). Кросс-секторные цепочки —
# главная ценность графа (сектор/пиры их не ловят).
SUPPLY_EDGES: list[tuple[str, str, float]] = [
    # Транснефть/Совкомфлот — логистика нефти для экспортёров.
    ("TRNFP", "ROSN", 0.4), ("TRNFP", "LKOH", 0.4), ("TRNFP", "GAZP", 0.4),
    ("TRNFP", "TATN", 0.4), ("TRNFP", "SNGS", 0.4),
    ("FLOT", "ROSN", 0.3), ("FLOT", "LKOH", 0.3), ("FLOT", "GAZP", 0.3),
    # Газ как топливо для тепловой генерации.
    ("GAZP", "IRAO", 0.3), ("GAZP", "UPRO", 0.3),
    # Сталь для застройщиков (строительный прокат).
    ("NLMK", "PIKK", 0.3), ("NLMK", "SMLT", 0.3),
    ("MAGN", "PIKK", 0.3), ("MAGN", "SMLT", 0.3),
    ("CHMF", "PIKK", 0.3), ("CHMF", "SMLT", 0.3),
    # Передача электроэнергии (сети) для генераторов.
    ("FEES", "IRAO", 0.25), ("FEES", "HYDR", 0.25), ("FEES", "UPRO", 0.25),
]

# competitor_of: (A, B, вес) — симметрично (раннер обходит обе стороны). Вес =
# близость рынков. Распространяется слабее supply-chain (со-движение сектора).
COMPETITOR_EDGES: list[tuple[str, str, float]] = [
    ("SBER", "VTBR", 0.5), ("SBER", "BSPB", 0.3), ("VTBR", "BSPB", 0.3),
    ("ROSN", "LKOH", 0.5), ("ROSN", "GAZP", 0.4), ("LKOH", "GAZP", 0.4),
    ("LKOH", "TATN", 0.4), ("ROSN", "TATN", 0.4),
    ("TATN", "SNGS", 0.4), ("LKOH", "SNGS", 0.4),
    ("NLMK", "MAGN", 0.6), ("NLMK", "CHMF", 0.6), ("MAGN", "CHMF", 0.6),
    ("GMKN", "RUAL", 0.3),
    ("PHOR", "AKRN", 0.6),
    ("IRAO", "UPRO", 0.4), ("IRAO", "HYDR", 0.3), ("UPRO", "HYDR", 0.3),
    ("MTSS", "RTKM", 0.5),
    ("YDEX", "VKCO", 0.5), ("POSI", "ASTR", 0.5),
    ("PIKK", "SMLT", 0.6),
]

# subsidiary_of: (дочерняя, материнская, вес). Холдинговая структура — новость материнской/
# дочерней со-движет связку (L2: «из чего состоит группа»). Храним одно направление
# (child→parent); раннер графа (graph_impact._neighbors) резолвит обе стороны. Только пары,
# где оба тикера есть в справочнике.
SUBSIDIARY_EDGES: list[tuple[str, str, float]] = [
    ("MTSS", "AFKS", 0.6),   # МТС — ключевой публичный актив АФК Система
    ("OZON", "AFKS", 0.5),   # Ozon — доля АФК Система
]


def seed_relations(session) -> int:
    """Идемпотентно сидит межэмитентные рёбра графа (G7). Возвращает число новых.

    Требует уже засеянных активов; неизвестные тикеры пропускает. ON CONFLICT по
    uq_relation — повторный запуск безопасен."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    ids = {a.ticker: a.id for a in session.scalars(select(Asset))}
    added = 0

    def add_edge(subj: str, pred: str, obj: str, weight: float) -> None:
        nonlocal added
        si, oi = ids.get(subj), ids.get(obj)
        if si is None or oi is None:
            return
        stmt = (
            pg_insert(Relation)
            .values(subject_type=EntityType.ASSET.value, subject_id=si,
                    predicate=pred,
                    object_type=EntityType.ASSET.value, object_id=oi,
                    weight=weight)
            .on_conflict_do_nothing(index_elements=[
                "subject_type", "subject_id", "predicate",
                "object_type", "object_id"])
        )
        if session.execute(stmt).rowcount:
            added += 1

    for a, b, w in SUPPLY_EDGES:
        add_edge(a, "supplier_of", b, w)
    for a, b, w in COMPETITOR_EDGES:
        add_edge(a, "competitor_of", b, w)  # одно направление; раннер симметричен
    for a, b, w in SUBSIDIARY_EDGES:
        add_edge(a, "subsidiary_of", b, w)  # child→parent; раннер резолвит обе стороны
    log.info("seed_relations_done", added=added)
    return added


def seed_database() -> int:
    """Заполняет справочник. Возвращает число добавленных активов."""
    added = 0
    with session_scope() as session:
        # Страны (идемпотентно по ISO-коду).
        for code, (cname, _aliases) in COUNTRIES.items():
            if session.scalars(select(Country).where(Country.code == code)).first() is None:
                session.add(Country(code=code, name=cname))

        # Макро-темы (идемпотентно по имени) — таксономия из nlp.themes.
        for tname, kws in THEME_KEYWORDS.items():
            if session.scalars(select(MacroTheme).where(MacroTheme.name == tname)).first() is None:
                session.add(MacroTheme(name=tname, keywords=kws))
        session.flush()

        # Наши эмитенты — российские: привязываем компании к стране РФ (для derived-связей
        # новость→страна и анализа экономики).
        rus = session.scalars(select(Country).where(Country.code == "RUS")).first()
        rus_id = rus.id if rus else None

        sector_cache: dict[str, Sector] = {}

        def get_sector(name: str) -> Sector:
            if name in sector_cache:
                return sector_cache[name]
            sec = session.scalars(select(Sector).where(Sector.name == name)).first()
            if sec is None:
                sec = Sector(name=name)
                session.add(sec)
                session.flush()
            sector_cache[name] = sec
            return sec

        for ticker, (name, sector_name, aliases) in ISSUERS.items():
            existing = session.scalars(select(Asset).where(Asset.ticker == ticker)).first()
            if existing is not None:
                continue
            sector = get_sector(sector_name)
            company = session.scalars(select(Company).where(Company.name == name)).first()
            if company is None:
                company = Company(name=name, aliases=aliases, sector_id=sector.id,
                                  country_id=rus_id)
                session.add(company)
                session.flush()
            asset = Asset(ticker=ticker, name=name, kind="share", board="TQBR",
                          company_id=company.id)
            session.add(asset)
            session.flush()
            # Связь графа: актив принадлежит сектору.
            session.add(Relation(
                subject_type=EntityType.ASSET.value, subject_id=asset.id,
                predicate="belongs_to",
                object_type=EntityType.SECTOR.value, object_id=sector.id,
            ))
            added += 1

        # Фонды денежного рынка: Asset kind="fund", доска TQTF, сектор «Денежный рынок».
        # Компания с пустыми алиасами (не линкуются в новостях) — нужна лишь для сектора.
        for ticker, name in MONEY_MARKET_FUNDS.items():
            if session.scalars(select(Asset).where(Asset.ticker == ticker)).first():
                continue
            sector = get_sector(MMF_SECTOR)
            company = session.scalars(select(Company).where(Company.name == name)).first()
            if company is None:
                company = Company(name=name, aliases=[], sector_id=sector.id,
                                  country_id=rus_id)
                session.add(company)
                session.flush()
            asset = Asset(ticker=ticker, name=name, kind="fund", board="TQTF",
                          company_id=company.id)
            session.add(asset)
            session.flush()
            session.add(Relation(
                subject_type=EntityType.ASSET.value, subject_id=asset.id,
                predicate="belongs_to",
                object_type=EntityType.SECTOR.value, object_id=sector.id,
            ))
            added += 1

        # C2: фьючерсы FORTS — Asset kind="future", доска RFUD, сектор «Срочный рынок».
        for ticker, (name, _code) in FUTURES.items():
            if session.scalars(select(Asset).where(Asset.ticker == ticker)).first():
                continue
            sector = get_sector(FUTURES_SECTOR)
            company = session.scalars(select(Company).where(Company.name == name)).first()
            if company is None:
                company = Company(name=name, aliases=[], sector_id=sector.id,
                                  country_id=rus_id)
                session.add(company)
                session.flush()
            asset = Asset(ticker=ticker, name=name, kind="future", board="RFUD",
                          company_id=company.id)
            session.add(asset)
            session.flush()
            session.add(Relation(
                subject_type=EntityType.ASSET.value, subject_id=asset.id,
                predicate="belongs_to",
                object_type=EntityType.SECTOR.value, object_id=sector.id,
            ))
            added += 1

        # Бенчмарк-индекс (без компании/сектора): только для alpha в бэктестах.
        if session.scalars(
            select(Asset).where(Asset.ticker == BENCHMARK_TICKER)
        ).first() is None:
            session.add(Asset(ticker=BENCHMARK_TICKER, name=BENCHMARK_NAME,
                              kind="index", board="SNDX"))
            added += 1
        session.flush()

        # G7: межэмитентные рёбра графа (идемпотентно; работает и на уже засеянной БД).
        rels_added = seed_relations(session)
    log.info("seed_done", assets_added=added, relations_added=rels_added)
    return added
