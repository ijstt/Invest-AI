"""Article.content_hash — нормализованный хеш заголовка для дедупа near-duplicate.

Одна новость от разных лент/источников (косметические отличия текста проходили
raw-дедуп по точному SHA256) создавала несколько статей и раздувала счётчики neg-spike
алертов. Колонка хранит нормализованный хеш заголовка; processing проверяет дубль в окне
`dedup_window_hours`. Индекс — под этот lookup.

Идемпотентна: колонка и индекс под IF NOT EXISTS. Бэкфилл значений и удаление
исторических дублей — отдельным шагом (`scripts/dedup_articles.py`), не в миграции.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-08
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_articles_content_hash "
        "ON articles (content_hash)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_articles_content_hash")
    op.execute("ALTER TABLE articles DROP COLUMN IF EXISTS content_hash")
