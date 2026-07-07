"""Эмбеддинги: vector(fp32) → halfvec(fp16) + HNSW-индекс.

Эмбеддинги — самая большая таблица (52% БД). halfvec вдвое легче по диску/памяти без
заметной потери recall для e5-large. Заодно меняем ivfflat (lists=100, переразбит на
текущем объёме) на HNSW — лучше recall/латентность, масштабируется автоматически.
Каст vector→halfvec без переэмбеддинга (fp32→fp16 для e5 безопасен).

Идемпотентна: на свежей БД колонка уже halfvec и HNSW-индекс создан в 0001 — все шаги
под guard'ами и становятся no-op.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-07
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) Сначала дропаем старый ivfflat-индекс: его opclass vector_cosine_ops не принимает
    #    halfvec, и смена типа колонки упала бы, пока индекс существует.
    op.execute("DROP INDEX IF EXISTS ix_embeddings_vector")
    # 2) Тип колонки → halfvec, только если сейчас vector (на свежей БД уже halfvec).
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'embeddings' AND column_name = 'vector'
                  AND udt_name = 'vector'
            ) THEN
                ALTER TABLE embeddings
                    ALTER COLUMN vector TYPE halfvec(1024) USING vector::halfvec(1024);
            END IF;
        END $$;
        """
    )
    # 3) HNSW-индекс на halfvec (IF NOT EXISTS — на свежей БД он создан в 0001).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_embeddings_vector "
        "ON embeddings USING hnsw (vector halfvec_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_embeddings_vector")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'embeddings' AND column_name = 'vector'
                  AND udt_name = 'halfvec'
            ) THEN
                ALTER TABLE embeddings
                    ALTER COLUMN vector TYPE vector(1024) USING vector::vector(1024);
            END IF;
        END $$;
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_embeddings_vector "
        "ON embeddings USING ivfflat (vector vector_cosine_ops) WITH (lists = 100)"
    )
