"""F7 (Волна 4): надёжность источника новостей.

«Источник» здесь тоньше, чем коннектор: для telegram это КАНАЛ (`source_ref`), для
RSS — имя источника (`source`). Надёжность = байесовская смесь:
- АПРИОРА доверия (ручные пометки из tg.txt: high/medium/low) и
- ЭМПИРИКИ — направленной точности по рыночным исходам E2 (news_outcomes): совпал ли
  знак тональности связи (статья, актив) со знаком market-adjusted доходности abn_5d.

Усадка к априору (`strength`) обязательна: телеграм-корпус ещё мал, у каналов по
несколько исходов — без усадки оценка шумная. Малое n → ≈ априор; много исходов →
≈ эмпирика. Чистые функции (`directional_accuracy`, `reliability_score`,
`credibility_multiplier`) — основной предмет тестов.

Применение: множитель достоверности в ранжировании сводки рынка (ненадёжные/слуховые
каналы тонут) и витрина `geo reliability`.
"""

from __future__ import annotations

from dataclasses import dataclass

from geoanalytics.core.types import Sentiment

# Уровни доверия (доли) и априоры по источникам из пометок пользователя (tg.txt).
TRUST_HIGH = 0.7
TRUST_MEDIUM = 0.5
TRUST_LOW = 0.3
DEFAULT_PRIOR = 0.5  # неизвестный источник — нейтрально

SOURCE_TRUST_PRIORS: dict[str, float] = {
    # Telegram-каналы (ключ = source_ref).
    "centralbank_russia": TRUST_HIGH,   # официоз ЦБ
    "prostoecon": TRUST_HIGH,           # аналитика, высокое доверие
    "ifax_go": TRUST_HIGH,              # Интерфакс, рынки
    "ecotopor": TRUST_MEDIUM,           # агрегатор, среднее
    # Агрегатор с сатирой/мнениями — чуть ниже high; rumor/opinion срежет ещё (factuality).
    "CAPITALIST_2033": 0.55,
    # F10: брокерские каналы — средне-высокое доверие, но «торгуют книгу» (позитивный
    # уклон) → прогнозы НЕ льём вслепую в общий сентимент (forecast-путь отдельно).
    "SberInvestments": 0.6,
    "tb_invest_official": 0.6,
    "bcs_world_of_investments": 0.6,
}

# Усадка эмпирики к априору, в «виртуальных наблюдениях».
DEFAULT_STRENGTH = 20
# Порог шума abn_5d (%): движения меньше — не считаем направленным исходом.
ABN_NOISE_PCT = 0.5

# Штраф достоверности по фактологичности (F4) для ранжирования.
_FACTUALITY_PENALTY = {"rumor": 0.7, "opinion": 0.6}


def trust_prior(source_key: str | None) -> float:
    """Априор доверия источника (доля). Неизвестный → DEFAULT_PRIOR."""
    if not source_key:
        return DEFAULT_PRIOR
    return SOURCE_TRUST_PRIORS.get(source_key, DEFAULT_PRIOR)


def directional_accuracy(pairs: list[tuple[str | None, float | None]]) -> tuple[int, int]:
    """(hits, n) по парам (тональность связи, abn_5d %).

    Учитываются только ненейтральные тональности с |abn| ≥ ABN_NOISE_PCT. hit —
    знак тональности совпал со знаком market-adjusted доходности. Чистая функция.
    """
    hits = n = 0
    for sentiment, abn in pairs:
        if abn is None or abs(abn) < ABN_NOISE_PCT:
            continue
        if sentiment == Sentiment.POSITIVE.value:
            direction = 1
        elif sentiment == Sentiment.NEGATIVE.value:
            direction = -1
        else:
            continue
        n += 1
        if direction * abn > 0:
            hits += 1
    return hits, n


def reliability_score(prior: float, hits: int, n: int,
                      *, strength: int = DEFAULT_STRENGTH) -> float:
    """Байесовская оценка надёжности с усадкой к априору.

    (hits + strength·prior) / (n + strength): n=0 → prior; n≫strength → эмпирика.
    """
    return round((hits + strength * prior) / (n + strength), 4)


def credibility_multiplier(reliability: float, factuality: str | None) -> float:
    """Множитель достоверности для ранжирования сводки, диапазон [0.5, 1.0].

    Надёжность источника → [0.6, 1.0], затем штраф за слух/мнение (F4). Не обнуляет
    (иначе теряем охват) — лишь опускает в выдаче.
    """
    rel = 0.6 + 0.4 * max(0.0, min(1.0, reliability))
    pen = _FACTUALITY_PENALTY.get(factuality or "fact", 1.0)
    return round(max(0.5, min(1.0, rel * pen)), 4)


@dataclass
class SourceReliability:
    source: str
    prior: float
    hits: int = 0
    n: int = 0
    accuracy: float | None = None   # hits/n, None при n=0
    score: float = 0.0
    rumor_share: float = 0.0        # доля factuality=rumor у источника
    articles: int = 0
    error: str | None = None


def source_reliability_report(session=None) -> list[SourceReliability]:
    """Надёжность по источникам: эмпирика E2 + априоры + доля слухов.

    Группировка по «ключу источника» = COALESCE(source_ref, source). Отсортировано по
    score убыв. Создаёт сессию при необходимости.
    """
    from sqlalchemy import func, select

    from geoanalytics.core.types import EntityType
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import (
        Article,
        ArticleEntity,
        NewsOutcome,
    )

    if session is None:
        with session_scope() as s:
            return source_reliability_report(s)

    src_key = func.coalesce(Article.source_ref, Article.source)

    # Эмпирика: пары (источник, тональность связи, abn_5d) по созревшим исходам.
    pairs_by_src: dict[str, list[tuple[str | None, float | None]]] = {}
    rows = session.execute(
        select(src_key, ArticleEntity.sentiment, NewsOutcome.abn_5d)
        .join(Article, Article.id == NewsOutcome.article_id)
        .join(ArticleEntity,
              (ArticleEntity.article_id == NewsOutcome.article_id)
              & (ArticleEntity.entity_id == NewsOutcome.asset_id)
              & (ArticleEntity.entity_type == EntityType.ASSET.value))
    )
    for source, sentiment, abn in rows:
        pairs_by_src.setdefault(source, []).append((sentiment, abn))

    # Доля слухов и общее число статей по источнику.
    fact_rows = session.execute(
        select(src_key, Article.factuality, func.count())
        .group_by(src_key, Article.factuality)
    )
    total_by_src: dict[str, int] = {}
    rumor_by_src: dict[str, int] = {}
    for source, factuality, cnt in fact_rows:
        total_by_src[source] = total_by_src.get(source, 0) + cnt
        if factuality == "rumor":
            rumor_by_src[source] = rumor_by_src.get(source, 0) + cnt

    reports: list[SourceReliability] = []
    for source in sorted(set(pairs_by_src) | set(total_by_src)):
        prior = trust_prior(source)
        hits, n = directional_accuracy(pairs_by_src.get(source, []))
        total = total_by_src.get(source, 0)
        rep = SourceReliability(
            source=source, prior=prior, hits=hits, n=n,
            accuracy=round(hits / n, 4) if n else None,
            score=reliability_score(prior, hits, n),
            rumor_share=round(rumor_by_src.get(source, 0) / total, 4) if total else 0.0,
            articles=total,
        )
        reports.append(rep)
    reports.sort(key=lambda r: r.score, reverse=True)
    return reports


def reliability_lookup(session) -> dict[str, float]:
    """{ключ источника: score} для ранжирования. Источники без исходов — априор."""
    return {r.source: r.score for r in source_reliability_report(session)}
