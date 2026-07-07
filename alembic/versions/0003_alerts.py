"""Алерты (M5.3): таблица `alerts` со сработавшими триггерами и статусом доставки.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-04
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Идемпотентно: на свежей БД 0001 (Base.metadata.create_all по актуальным моделям)
    # уже создаёт таблицу alerts. На БД, застрявшей в 0002, она создаётся здесь.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id          BIGSERIAL PRIMARY KEY,
            alert_type  VARCHAR(32) NOT NULL,
            ticker      VARCHAR(32),
            severity    VARCHAR(16) NOT NULL DEFAULT 'info',
            title       VARCHAR(512) NOT NULL,
            message     TEXT NOT NULL,
            dedup_key   VARCHAR(128) NOT NULL,
            payload     JSON,
            channels    JSON,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_alert_dedup UNIQUE (dedup_key)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_alerts_created ON alerts (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_alerts_alert_type ON alerts (alert_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_alerts_ticker ON alerts (ticker)")


def downgrade() -> None:
    op.drop_table("alerts")
