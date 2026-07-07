"""F2 (Волна 2): салиентность связи статья↔сущность — article_entities.salient.

TRUE — объект главный в новости, FALSE — фоновое упоминание (список котировок,
перечисление), NULL — классификатор не настроен/не прогонялся (трактуется
потребителями как «салиентно» — поведение до F2).

Идемпотентна: IF NOT EXISTS.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-11
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE article_entities ADD COLUMN IF NOT EXISTS salient BOOLEAN")


def downgrade() -> None:
    op.execute("ALTER TABLE article_entities DROP COLUMN IF EXISTS salient")
