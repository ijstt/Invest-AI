"""Список активов для автодополнения тикеров (M6): CLI/API/веб-формы."""

from __future__ import annotations

from sqlalchemy import asc, select

from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import Asset, Company, Sector


def list_assets() -> list[dict]:
    """Все активы (тикер, название, сектор) по алфавиту тикера — для datalist/автодополнения.

    Сектор берётся через компанию-эмитента (Asset → Company → Sector).
    """
    with session_scope() as session:
        rows = session.execute(
            select(Asset.ticker, Asset.name, Sector.name)
            .select_from(Asset)
            .join(Company, Company.id == Asset.company_id, isouter=True)
            .join(Sector, Sector.id == Company.sector_id, isouter=True)
            .order_by(asc(Asset.ticker))
        )
        return [{"ticker": t, "name": n, "sector": sec} for t, n, sec in rows]
