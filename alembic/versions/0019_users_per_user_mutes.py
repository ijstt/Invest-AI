"""Волна 5b: таблица users (идентичность Telegram) + per-user измерение alert_mutes.

users — пользователи бота: telegram_user_id (в личке = chat_id), allowed (авторизация),
role (admin/user). alert_mutes.user_id — NULL=глобальный mute (прежнее поведение), иначе
личный mute пользователя (не доставляется только ему).

Идемпотентна: CREATE TABLE / ADD COLUMN IF NOT EXISTS.

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-15
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id               BIGSERIAL PRIMARY KEY,
            telegram_user_id BIGINT NOT NULL,
            chat_id          VARCHAR(32) NOT NULL,
            username         VARCHAR(128),
            role             VARCHAR(16) NOT NULL DEFAULT 'user',
            allowed          BOOLEAN NOT NULL DEFAULT false,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_user_tg UNIQUE (telegram_user_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_chat ON users (chat_id)")
    op.execute(
        "ALTER TABLE alert_mutes ADD COLUMN IF NOT EXISTS "
        "user_id BIGINT REFERENCES users(id) ON DELETE CASCADE"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_alert_mutes_user ON alert_mutes (user_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_alert_mutes_user")
    op.execute("ALTER TABLE alert_mutes DROP COLUMN IF EXISTS user_id")
    op.execute("DROP TABLE IF EXISTS users")
