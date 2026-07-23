"""Трек 2 / Рекавери Kill-Switch: добавление resumed_at и baseline_equity в futures_risk_state.

Revision ID: 0038
Revises: 0037
Create Date: 2026-07-23
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE futures_risk_state ADD COLUMN IF NOT EXISTS resumed_at TIMESTAMPTZ")
    op.execute("ALTER TABLE futures_risk_state ADD COLUMN IF NOT EXISTS baseline_equity DOUBLE PRECISION")


def downgrade() -> None:
    op.execute("ALTER TABLE futures_risk_state DROP COLUMN IF EXISTS resumed_at")
    op.execute("ALTER TABLE futures_risk_state DROP COLUMN IF EXISTS baseline_equity")
