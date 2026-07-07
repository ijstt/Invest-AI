"""G7 (Волна 4): уникальность ребра графа для идемпотентного сида связей.

relations пополняется межэмитентными рёбрами (competitor_of, supplier_of).
UNIQUE по (subject_type, subject_id, predicate, object_type, object_id) делает
повторный `geo db seed` безопасным (ON CONFLICT DO NOTHING).

Идемпотентна: IF NOT EXISTS. Перед созданием констрейнта чистим возможные дубли.

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-13
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # На всякий случай убираем дубли рёбер до создания UNIQUE (оставляем min id).
    op.execute("""
        DELETE FROM relations a USING relations b
        WHERE a.id > b.id
          AND a.subject_type = b.subject_type AND a.subject_id = b.subject_id
          AND a.predicate = b.predicate
          AND a.object_type = b.object_type AND a.object_id = b.object_id
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_relation
        ON relations (subject_type, subject_id, predicate, object_type, object_id)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_relation")
