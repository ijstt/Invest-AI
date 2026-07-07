"""F6 (Волна 2): сюжеты — таблица stories + articles.story_id.

Кластеризация статей в сюжеты (одно событие = N статей/рерайтов): лечит
double-counting neg_spike (Б10) и рерайты мимо хеш-дедупа (Б11).

Идемпотентна: IF NOT EXISTS.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-11
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS stories (
            id           BIGSERIAL PRIMARY KEY,
            title        VARCHAR(1024) NOT NULL,
            started_at   TIMESTAMPTZ,
            last_seen_at TIMESTAMPTZ,
            n_articles   INTEGER NOT NULL DEFAULT 1
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_stories_started ON stories (started_at)")
    op.execute(
        "ALTER TABLE articles ADD COLUMN IF NOT EXISTS story_id BIGINT "
        "REFERENCES stories(id) ON DELETE SET NULL"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_articles_story ON articles (story_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_articles_story")
    op.execute("ALTER TABLE articles DROP COLUMN IF EXISTS story_id")
    op.execute("DROP TABLE IF EXISTS stories")
