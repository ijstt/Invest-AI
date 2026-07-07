"""L3: кросс-секционные факторные скоры активов во времени (factor_scores).

Стандартизованные по вселенной акций факторы value/quality/growth + композит, снимок на день.
Закрывает провал «картина во времени почти не накапливается»: таблица копит дневные срезы
факторных экспозиций → тренд. Одна строка на (день, актив, фактор), идемпотентно.

Идемпотентна: CREATE TABLE / INDEX IF NOT EXISTS.

Revision ID: 0028
Revises: 0027
Create Date: 2026-06-19
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS factor_scores ("
        " id BIGSERIAL PRIMARY KEY,"
        " day DATE NOT NULL,"
        " asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,"
        " factor VARCHAR(16) NOT NULL,"              # value/quality/growth/composite
        " zscore DOUBLE PRECISION NOT NULL,"
        " percentile DOUBLE PRECISION,"              # ранг по вселенной, 0..100
        " created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_factor_day_asset_factor "
        "ON factor_scores (day, asset_id, factor)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_factor_day_asset ON factor_scores (day, asset_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS factor_scores")
