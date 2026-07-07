"""F4+F7 (Волна 4): articles.factuality + articles.source_ref.

factuality — fact/rumor/opinion (NULL — не размечена), правила nlp/rumor.py.
source_ref — идентичность источника тоньше source: канал для telegram (NULL у RSS).
Существующие telegram-строки бэкфиллим каналом из URL (t.me/<канал>/<id>).

Идемпотентна: IF NOT EXISTS.

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-13
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS factuality VARCHAR(16)")
    op.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS source_ref VARCHAR(64)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_articles_source_ref ON articles (source_ref)"
    )
    # Бэкфилл канала для уже сохранённых telegram-постов: url вида t.me/<канал>/<id>.
    op.execute(r"""
        UPDATE articles
           SET source_ref = substring(url FROM 't\.me/([^/]+)/')
         WHERE source = 'telegram'
           AND source_ref IS NULL
           AND url ~ 't\.me/[^/]+/'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_articles_source_ref")
    op.execute("ALTER TABLE articles DROP COLUMN IF EXISTS source_ref")
    op.execute("ALTER TABLE articles DROP COLUMN IF EXISTS factuality")
