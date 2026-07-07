"""Снимок макропоказателей для оверлея в аналитике актива.

Собирает последние значения ключевой ставки и курсов валют из нормализованных
таблиц. В дальнейшем (M3+) сюда добавятся нефть (Brent/Urals), инфляция, ставки
мировых ЦБ и т.п.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from geoanalytics.storage.models import FxRate, MacroSeries

# Сырьевые товары и внешние ставки уже собираются коннекторами (commodities/fred/ecb) и
# лежат в MacroSeries, но раньше не выгружались в снимок. Теперь — для богатой аналитики.
_COMMODITY_INDICATORS = ("brent", "gold", "silver", "platinum", "palladium")
_EXTERNAL_RATE_INDICATORS = ("fed_funds", "us_10y", "ecb_dfr", "ecb_mrr")


@dataclass
class MacroSnapshot:
    key_rate: float | None = None
    key_rate_date: str | None = None
    fx: dict[str, float] = field(default_factory=dict)
    commodities: dict[str, float] = field(default_factory=dict)      # brent/gold/silver
    external_rates: dict[str, float] = field(default_factory=dict)   # fed_funds/us_10y/ecb_*

    def as_dict(self) -> dict:
        d = {"fx": self.fx}
        if self.key_rate is not None:
            d["key_rate"] = self.key_rate
        if self.commodities:
            d["commodities"] = self.commodities
        if self.external_rates:
            d["external_rates"] = self.external_rates
        return d


def _latest_series_values(session: Session, indicators: tuple[str, ...]) -> dict[str, float]:
    """Последнее значение по каждому индикатору из набора (max(ts) per indicator)."""
    if not indicators:
        return {}
    latest_ts = (
        select(MacroSeries.indicator, func.max(MacroSeries.ts).label("ts"))
        .where(MacroSeries.indicator.in_(indicators))
        .group_by(MacroSeries.indicator).subquery()
    )
    rows = session.scalars(
        select(MacroSeries).join(
            latest_ts,
            (MacroSeries.indicator == latest_ts.c.indicator)
            & (MacroSeries.ts == latest_ts.c.ts),
        )
    )
    return {s.indicator: float(s.value) for s in rows}


def macro_snapshot(session: Session) -> MacroSnapshot:
    """Последние ставка, курсы валют, сырьё и внешние ставки."""
    snap = MacroSnapshot()
    rate = session.scalars(
        select(MacroSeries).where(MacroSeries.indicator == "key_rate")
        .order_by(desc(MacroSeries.ts)).limit(1)
    ).first()
    if rate is not None:
        snap.key_rate = float(rate.value)
        snap.key_rate_date = rate.ts.strftime("%d.%m.%Y")

    latest_ts = (
        select(FxRate.currency, func.max(FxRate.ts).label("ts"))
        .group_by(FxRate.currency).subquery()
    )
    rows = session.scalars(
        select(FxRate).join(
            latest_ts,
            (FxRate.currency == latest_ts.c.currency) & (FxRate.ts == latest_ts.c.ts),
        )
    )
    for fx in rows:
        snap.fx[fx.currency] = float(fx.value)

    snap.commodities = _latest_series_values(session, _COMMODITY_INDICATORS)
    snap.external_rates = _latest_series_values(session, _EXTERNAL_RATE_INDICATORS)
    return snap
