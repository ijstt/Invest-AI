"""Начальная схема: все таблицы + расширения + hypertable'ы + индекс вектора.

Revision ID: 0001
Revises:
Create Date: 2026-06-03
"""
from collections.abc import Sequence

from alembic import op
from geoanalytics.storage.models import Base

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Тайм-серии, которые превращаем в hypertable'ы TimescaleDB.
HYPERTABLES = ["prices", "macro_series", "fx_rates"]


def upgrade() -> None:
    bind = op.get_bind()

    # Расширения (на случай, если init-скрипт контейнера не отработал).
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Создаём все таблицы по метаданным моделей.
    Base.metadata.create_all(bind=bind)

    # Превращаем тайм-серии в hypertable'ы (партиционирование по времени).
    for table in HYPERTABLES:
        op.execute(
            f"SELECT create_hypertable('{table}', 'ts', "
            f"if_not_exists => TRUE, migrate_data => TRUE)"
        )

    # Индекс для семантического поиска по эмбеддингам (косинусное расстояние).
    # Колонка vector — halfvec (см. models.EMBEDDING_DIM), индекс HNSW: лучше recall/
    # латентность, чем ivfflat, и сам масштабируется. Существующие БД переводит миграция 0007.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_embeddings_vector "
        "ON embeddings USING hnsw (vector halfvec_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_embeddings_vector")
    Base.metadata.drop_all(bind=op.get_bind())
