"""J1 (Волна 4): виртуальный портфель — таблица portfolio_positions.

Однопользовательская система → один портфель, одна строка на актив
(UNIQUE(asset_id), `geo portfolio add` делает upsert). avg_price опциональна —
без неё позиция учитывается в стоимости/риске, но без P&L. Не hypertable:
это не тайм-серия, а текущее состояние.

Идемпотентна: IF NOT EXISTS.

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-12
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_positions (
            id         SERIAL PRIMARY KEY,
            asset_id   INTEGER NOT NULL UNIQUE REFERENCES assets(id) ON DELETE CASCADE,
            quantity   DOUBLE PRECISION NOT NULL,
            avg_price  NUMERIC(18,6),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS portfolio_positions")
