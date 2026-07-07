"""Волна 5c: per-user портфель — привязка portfolio_positions к пользователю.

`user_id` NULL — портфель «владельца» (дашборд/CLI, прежнее поведение); иначе личный портфель
бот-пользователя. Уникальность меняется с (asset_id) на (COALESCE(user_id,0), asset_id):
одна строка на актив В РАМКАХ портфеля. Существующие позиции остаются user_id=NULL (владелец).

Идемпотентна: ADD COLUMN / DROP CONSTRAINT / CREATE INDEX IF [NOT] EXISTS.

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-15
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE portfolio_positions ADD COLUMN IF NOT EXISTS "
        "user_id BIGINT REFERENCES users(id) ON DELETE CASCADE"
    )
    # Старая уникальность (один портфель) → уникальность в рамках портфеля (user, asset).
    op.execute("ALTER TABLE portfolio_positions DROP CONSTRAINT IF EXISTS "
               "portfolio_positions_asset_id_key")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_portfolio_user_asset "
               "ON portfolio_positions (COALESCE(user_id, 0), asset_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_portfolio_user "
               "ON portfolio_positions (user_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_portfolio_user")
    op.execute("DROP INDEX IF EXISTS uq_portfolio_user_asset")
    op.execute("ALTER TABLE portfolio_positions DROP COLUMN IF EXISTS user_id")
    # NB: исходную unique(asset_id) downgrade не возвращает (могут быть per-user дубли).
