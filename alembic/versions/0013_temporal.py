"""F3 (Волна 3): temporal anchoring — articles.temporal_status + articles.event_date.

temporal_status — past/future/forecast/none (NULL — модель не настроена);
event_date — дата СОБЫТИЯ, извлечённая из текста (NULL — не извлечена, событие
считается днём публикации — поведение до F3).

Идемпотентна: IF NOT EXISTS.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-11
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS temporal_status VARCHAR(8)")
    op.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS event_date DATE")


def downgrade() -> None:
    op.execute("ALTER TABLE articles DROP COLUMN IF EXISTS event_date")
    op.execute("ALTER TABLE articles DROP COLUMN IF EXISTS temporal_status")
