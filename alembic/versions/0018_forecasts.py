"""F10 (Волна 4): хранилище прогнозов брокеров + флаг is_forecast на статьях.

forecasts — ожидания брокерских каналов (целевая цена/дивиденд/ставка) для (статья,
актив); позже сравниваются с фактом (surprise). articles.is_forecast помечает прогноз-
посты, чтобы не лить их в общий сентимент.

Идемпотентна: IF NOT EXISTS / ADD COLUMN IF NOT EXISTS.

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-14
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE articles ADD COLUMN IF NOT EXISTS "
        "is_forecast BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS forecasts (
            id             BIGSERIAL PRIMARY KEY,
            article_id     BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
            asset_id       INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
            kind           VARCHAR(16) NOT NULL,
            value          DOUBLE PRECISION NOT NULL,
            unit           VARCHAR(8) NOT NULL,
            target_date    DATE,
            source_channel VARCHAR(64),
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_forecast UNIQUE (article_id, asset_id, kind, value)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_forecast_asset ON forecasts (asset_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_forecast_kind ON forecasts (kind)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS forecasts")
    op.execute("ALTER TABLE articles DROP COLUMN IF EXISTS is_forecast")
