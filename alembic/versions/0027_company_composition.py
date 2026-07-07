"""L2: состав и бизнес компании (профиль Company + revenue_segments).

Долгосрочная (Aladdin-подобная) ветка, фаза L2 — «из чего состоит компания».
1) Расширяем плоскую `companies` снапшот-профилем эмитента: описание, капитализация,
   free-float, число акций (заполняется из smart-lab/вручную).
2) Новая таблица `revenue_segments` — сегменты выручки (одна строка на
   компания/сегмент/период/источник, идемпотентно). Холдинговые рёбра
   subsidiary_of/parent_of используют существующую таблицу `relations` (схема не меняется).

Идемпотентна: ADD COLUMN / CREATE TABLE / INDEX IF NOT EXISTS.

Revision ID: 0027
Revises: 0026
Create Date: 2026-06-19
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) Профиль эмитента на companies.
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS description TEXT")
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS market_cap DOUBLE PRECISION")
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS free_float DOUBLE PRECISION")
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS shares DOUBLE PRECISION")

    # 2) Сегменты выручки.
    op.execute(
        "CREATE TABLE IF NOT EXISTS revenue_segments ("
        " id BIGSERIAL PRIMARY KEY,"
        " company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,"
        " segment VARCHAR(64) NOT NULL,"
        " value DOUBLE PRECISION NOT NULL,"           # выручка сегмента, ₽
        " share DOUBLE PRECISION,"                    # доля сегмента в выручке, %
        " period VARCHAR(16),"                        # 2024 | 2024-H1 | NULL
        " source VARCHAR(64) NOT NULL DEFAULT 'manual',"
        " created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_segment_company_name_period "
        "ON revenue_segments (company_id, segment, COALESCE(period, ''), source)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS revenue_segments")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS shares")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS free_float")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS market_cap")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS description")
