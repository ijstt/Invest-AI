"""Трек 2 / Объективный вход (A5): прозрачная разбивка conviction в журнале сделок.

Две nullable-колонки на `futures_paper_trades`: `conviction` (0–1, уверенность совокупности
независимых доказательств на входе) и `conviction_drivers` (JSON-разбивка: метка/знак/вклад).
Нужны для аудита «почему вошли» и колонки conviction на панели /ui/track2. Старые ряды — NULL.

Revision ID: 0036
Revises: 0035
Create Date: 2026-06-22
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE futures_paper_trades "
               "ADD COLUMN IF NOT EXISTS conviction DOUBLE PRECISION")
    op.execute("ALTER TABLE futures_paper_trades "
               "ADD COLUMN IF NOT EXISTS conviction_drivers JSON")


def downgrade() -> None:
    op.execute("ALTER TABLE futures_paper_trades DROP COLUMN IF EXISTS conviction_drivers")
    op.execute("ALTER TABLE futures_paper_trades DROP COLUMN IF EXISTS conviction")
