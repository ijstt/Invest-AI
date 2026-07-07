"""B3: вывод прогнозов брокеров (F10) в оборот — таргеты + сюрприз (факт − прогноз).

Раньше таблица `forecasts` (целевые цены/дивиденды из брокерских каналов) заполнялась, но
нигде не читалась («насухо»). Здесь прогнозы по активу выводятся в карточку: потенциал к
текущей цене (для актуальных таргетов) и сюрприз (насколько факт разошёлся с прогнозом, когда
горизонт `target_date` наступил). Методология «данные → модель»: накопленный сюрприз — сигнал
надёжности источника и вход рекомендаций (Волна C).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from geoanalytics.storage.models import Article, Forecast

_KIND_LABEL = {"target_price": "Целевая цена", "dividend": "Дивиденд", "key_rate": "Ставка"}


def forecasts_for_asset(session: Session, asset_id: int, last_price: float | None = None,
                        limit: int = 6) -> list[dict]:
    """Последние прогнозы брокеров по активу с потенциалом и сюрпризом (факт − прогноз).

    Для целевой цены: `implied_pct` — потенциал к текущей цене (value/last − 1); если горизонт
    `target_date` уже наступил, `surprise_pct` — насколько текущая цена разошлась с прогнозом
    (>0 — рынок выше таргета). Текущая цена — приближение факта (точную цену на дату не тянем).
    """
    today = datetime.now(UTC).date()
    rows = session.execute(
        select(Forecast.kind, Forecast.value, Forecast.unit, Forecast.target_date,
               Forecast.source_channel, Article.url, Article.published_at)
        .join(Article, Article.id == Forecast.article_id)
        .where(Forecast.asset_id == asset_id)
        .order_by(desc(Forecast.created_at))
        .limit(limit)
    ).all()
    out: list[dict] = []
    for kind, value, unit, target_date, source, url, published_at in rows:
        item = {
            "kind": kind, "label": _KIND_LABEL.get(kind, kind), "value": value, "unit": unit,
            "target_date": target_date, "source": source, "url": url,
            "published_at": published_at, "implied_pct": None, "surprise_pct": None,
            "matured": bool(target_date and target_date <= today),
        }
        if kind == "target_price" and last_price:
            item["implied_pct"] = round((value / last_price - 1.0) * 100, 1)  # потенциал к таргету
            if item["matured"]:
                item["surprise_pct"] = round((last_price - value) / value * 100, 1)
        out.append(item)
    return out
