"""Унифицированный доступ к факторным сериям (сырьё и валюты).

Сводит в один контракт ряды, которые иначе разбросаны по разным таблицам и конвенциям:
brent — `macro_series`; драгметаллы — учётные цены ЦБ, приведённые к мировой цене в USD
(как в корреляциях, с поправкой лага публикации); валюты — официальные курсы ЦБ и кросс
USD/EUR из тех же серий. Чистый слой поверх загрузчиков `correlations` — переиспользуется
страницей «Факторы» дашборда и факторными объяснениями ask (companion-пререквизит роудмапа).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from geoanalytics.analytics.correlations import (
    _cross_levels,
    _fx_levels,
    _macro_levels,
    _world_metal_levels,
)


@dataclass
class FactorSeries:
    """Один факторный ряд: метаданные + выровненные по датам уровни."""

    key: str
    label: str
    unit: str
    group: str            # "commodity" | "fx"
    dates: list[date]
    values: list[float]

    @property
    def last(self) -> float | None:
        return self.values[-1] if self.values else None

    @property
    def change_pct(self) -> float | None:
        """Изменение за весь показанный период (первый → последний), %."""
        if len(self.values) >= 2 and self.values[0]:
            return round((self.values[-1] - self.values[0]) / self.values[0] * 100, 2)
        return None


# key, label, unit, group, loader(session) -> dict[date, float]
_DEFS = [
    ("brent", "Brent", "$/барр.", "commodity", lambda s: _macro_levels(s, "brent")),
    ("gold", "Золото", "$/г", "commodity", lambda s: _world_metal_levels(s, "gold")),
    ("silver", "Серебро", "$/г", "commodity", lambda s: _world_metal_levels(s, "silver")),
    ("platinum", "Платина", "$/г", "commodity", lambda s: _world_metal_levels(s, "platinum")),
    ("palladium", "Палладий", "$/г", "commodity", lambda s: _world_metal_levels(s, "palladium")),
    ("USD", "USD/RUB", "₽", "fx", lambda s: _fx_levels(s, "USD")),
    ("EUR", "EUR/RUB", "₽", "fx", lambda s: _fx_levels(s, "EUR")),
    ("CNY", "CNY/RUB", "₽", "fx", lambda s: _fx_levels(s, "CNY")),
    ("usd_eur", "USD/EUR", "", "fx", lambda s: _cross_levels(s, "USD", "EUR")),
]


def factor_series(session: Session, *, lookback_days: int = 365) -> list[FactorSeries]:
    """Факторные ряды за последние `lookback_days` календарных дней, отсортированы по дате.

    Ряд без данных за окно возвращается с пустыми `dates`/`values` (страница покажет прочерк).
    """
    cutoff = date.today() - timedelta(days=lookback_days)
    out: list[FactorSeries] = []
    for key, label, unit, group, loader in _DEFS:
        levels = loader(session)
        dates = sorted(d for d in levels if d >= cutoff)
        out.append(FactorSeries(key, label, unit, group, dates,
                                [levels[d] for d in dates]))
    return out
