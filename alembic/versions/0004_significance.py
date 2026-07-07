"""Значимость новости (M6): колонка articles.significance + индекс.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-04
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Идемпотентно: на свежей БД 0001 (create_all по актуальным моделям) колонка уже есть.
    op.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS significance DOUBLE PRECISION")
    op.execute("CREATE INDEX IF NOT EXISTS ix_articles_significance ON articles (significance)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_articles_significance")
    op.execute("ALTER TABLE articles DROP COLUMN IF EXISTS significance")
