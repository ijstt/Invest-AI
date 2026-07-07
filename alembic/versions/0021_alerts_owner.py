"""Волна 5c (хвост): adресный владелец алерта — alerts.user_id.

`user_id` NULL — broadcast-алерт (рыночный/новостной, всем allowed-юзерам, прежнее поведение);
иначе персональный алерт владельцу портфеля (доставляется ТОЛЬКО ему). Нужен для портфельных
алертов per-owner: каждый видит просадку/минус СВОЕГО портфеля.

Идемпотентна: ADD COLUMN / CREATE INDEX IF NOT EXISTS.

Revision ID: 0021
Revises: 0020
Create Date: 2026-06-15
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS "
        "user_id BIGINT REFERENCES users(id) ON DELETE CASCADE"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_alerts_user ON alerts (user_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_alerts_user")
    op.execute("ALTER TABLE alerts DROP COLUMN IF EXISTS user_id")
