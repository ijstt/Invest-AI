"""Трек 2 / Пул 8: снимки эквити бумажного счёта (трек-рекорд песочницы).

Закрывает «эквити считается на лету и не сохраняется»: один ряд на (счёт, час) с mark-to-market
эквити, пиком и просадкой — кривая, по которой доказываем результативность за время созревания
(и источник для будущей панели дашборда). Идемпотентно по (account, ts) — повтор в тот же час
перезаписывает снимок.

Revision ID: 0034
Revises: 0033
Create Date: 2026-06-21
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS futures_paper_equity ("
        " id BIGSERIAL PRIMARY KEY,"
        " account VARCHAR(32) NOT NULL,"
        " ts TIMESTAMPTZ NOT NULL,"                  # час снимка (усечён)
        " equity DOUBLE PRECISION NOT NULL,"         # mark-to-market эквити
        " realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,"
        " unrealized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,"
        " open_positions INTEGER NOT NULL DEFAULT 0,"
        " peak_equity DOUBLE PRECISION NOT NULL,"    # пик эквити до этого момента
        " drawdown_pct DOUBLE PRECISION NOT NULL DEFAULT 0,"
        " gross_margin DOUBLE PRECISION NOT NULL DEFAULT 0,"  # валовая занятая маржа
        " created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_futpaperequity_key "
        "ON futures_paper_equity (account, ts)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS futures_paper_equity")
