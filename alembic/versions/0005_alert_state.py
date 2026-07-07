"""Состояние алертов (UX-слой): alerts.acknowledged_at + таблица alert_mutes.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-06
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Идемпотентно: на свежей БД 0001 (create_all по актуальным моделям) уже всё есть.
    op.execute("ALTER TABLE alerts ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMPTZ")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS alert_mutes (
            id          BIGSERIAL PRIMARY KEY,
            scope_type  VARCHAR(16) NOT NULL,
            scope_value VARCHAR(96) NOT NULL,
            reason      VARCHAR(256),
            until       TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_alert_mutes_scope "
        "ON alert_mutes (scope_type, scope_value)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS alert_mutes")
    op.execute("ALTER TABLE alerts DROP COLUMN IF EXISTS acknowledged_at")
