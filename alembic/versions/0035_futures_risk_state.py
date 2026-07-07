"""Трек 2 / Пул 9 / B: kill-switch состояние бумажного счёта (безоператорная защита).

Один ряд на счёт: `halted` блокирует новые входы при пробое жёстких лимитов/аномалии; сбрасывается
вручную (`geo futures-intraday resume`). Причина и время — для аудита.

Revision ID: 0035
Revises: 0034
Create Date: 2026-06-21
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS futures_risk_state ("
        " account VARCHAR(32) PRIMARY KEY,"
        " halted BOOLEAN NOT NULL DEFAULT false,"
        " reason VARCHAR(128),"
        " updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS futures_risk_state")
