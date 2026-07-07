"""F5 (Волна 3): numeric extraction — таблица article_numbers.

Числовые факты из текста статьи (rule-based, nlp/numeric.py):
дивиденд на акцию (RUB), ключевая ставка (pct), сумма сделки (RUB/USD/EUR/CNY).
Уникальность (article_id, kind, value, unit) — повторная разметка идемпотентна.

Идемпотентна: IF NOT EXISTS.

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-11
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS article_numbers (
            id         BIGSERIAL PRIMARY KEY,
            article_id BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
            kind       VARCHAR(16) NOT NULL,
            value      DOUBLE PRECISION NOT NULL,
            unit       VARCHAR(8) NOT NULL,
            snippet    VARCHAR(200),
            CONSTRAINT uq_artnum UNIQUE (article_id, kind, value, unit)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_artnum_kind ON article_numbers (kind)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS article_numbers")
