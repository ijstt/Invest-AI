"""Макро-темы (связь новость↔тема): таблица `macro_themes`.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-06
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Идемпотентно: на свежей БД 0001 (create_all по моделям) уже создаёт таблицу.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS macro_themes (
            id        SERIAL PRIMARY KEY,
            name      VARCHAR(64) NOT NULL,
            keywords  JSON,
            CONSTRAINT uq_macro_theme_name UNIQUE (name)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS macro_themes")
