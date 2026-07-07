"""L5: история режимов рынка во времени (market_regimes).

Материализует разметку HMM (G2) по дням: режим (спокойный/переходный/кризис), его vol.
Закрывает провал «режимы считаются на лету и выбрасываются» — копит историю для тренда и
бэктеста. Один ряд на день, идемпотентно (HMM переразмечает всю историю при пересчёте).

Идемпотентна: CREATE TABLE / INDEX IF NOT EXISTS.

Revision ID: 0029
Revises: 0028
Create Date: 2026-06-19
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS market_regimes ("
        " id BIGSERIAL PRIMARY KEY,"
        " day DATE NOT NULL,"
        " state INTEGER NOT NULL,"               # 0=спокойный … K-1=кризис
        " label VARCHAR(32) NOT NULL,"
        " vol DOUBLE PRECISION,"                 # ср. vol IMOEX режима, %/день
        " created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_market_regime_day ON market_regimes (day)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS market_regimes")
