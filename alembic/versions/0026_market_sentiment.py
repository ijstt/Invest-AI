"""Персистентный индекс настроения рынка (market_sentiment) — Волна B/B1.

Раньше сентимент жил только по-документно (`articles.sentiment_score`), а агрегат во времени
считался на лету (EWMA) и не сохранялся. Эта таблица материализует дневной индекс настроения
по областям: рынок целиком / сектор / отдельный актив. Накопленный ряд даёт тренд, ширину
(breadth = доля позитив − негатив) и дивергенцию (цена ↔ настроение) — вход в консенсус сводки
и рекомендации (Волна C). Заполняется ежедневным джобом + бэкфилл-командой.

Идемпотентна: CREATE TABLE / INDEX IF NOT EXISTS; уникальность (day, scope, asset_id, sector)
позволяет переписывать день при пересчёте (UPSERT).

Revision ID: 0026
Revises: 0025
Create Date: 2026-06-16
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS market_sentiment ("
        " id BIGSERIAL PRIMARY KEY,"
        " day DATE NOT NULL,"
        " scope VARCHAR(8) NOT NULL,"               # market | sector | asset
        " asset_id INTEGER REFERENCES assets(id) ON DELETE CASCADE,"  # NULL для market/sector
        " sector VARCHAR(64),"                      # NULL для market/asset
        " sent_mean DOUBLE PRECISION NOT NULL,"     # среднее sentiment_score [-1..1]
        " sent_ewma DOUBLE PRECISION NOT NULL,"     # EWMA по дням (тональный моментум)
        " breadth DOUBLE PRECISION NOT NULL,"       # доля позитив − негатив [-1..1]
        " dispersion DOUBLE PRECISION NOT NULL,"    # σ sentiment_score за день (разброс мнений)
        " n_docs INTEGER NOT NULL,"                 # документов в агрегате
        " pressure_sum DOUBLE PRECISION NOT NULL,"  # Σ significance (вес новостного фона)
        " created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )
    # UPSERT-ключ: один ряд на (день, область, актив, сектор); COALESCE — иначе NULL ≠ NULL.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_market_sentiment "
        "ON market_sentiment (day, scope, COALESCE(asset_id, 0), COALESCE(sector, ''))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_market_sentiment_scope_day "
        "ON market_sentiment (scope, day DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS market_sentiment")
