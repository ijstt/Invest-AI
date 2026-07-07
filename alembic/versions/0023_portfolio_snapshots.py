"""Хвост качественного просмотра портфеля: ежедневные снимки стоимости (portfolio_snapshots).

Реальная история стоимости портфеля во времени — вместо реконструкции по текущему составу
(scale кривой капитала к текущей сумме, допущение «состав не менялся»). Дневной job пишет одну
строку на (портфель, дату): рыночная стоимость + база покупки (cost_basis) для P&L во времени.
`user_id` NULL — портфель ВЛАДЕЛЬЦА (дашборд/CLI), иначе личный бот-пользователя (как 5c/0022).
Уникальность — один снимок на дату В РАМКАХ портфеля: UNIQUE(COALESCE(user_id,0), snapshot_date)
(тот же инвариант id≥1 у users, что в 0020/0022).

Идемпотентна: CREATE TABLE / INDEX IF NOT EXISTS.

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-16
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS portfolio_snapshots ("
        " id BIGSERIAL PRIMARY KEY,"
        " user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,"
        " snapshot_date DATE NOT NULL,"
        " total_value_rub NUMERIC(18,2) NOT NULL,"
        " cost_basis_rub NUMERIC(18,2),"
        " created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_pf_snapshot_user_date "
        "ON portfolio_snapshots (COALESCE(user_id, 0), snapshot_date)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS portfolio_snapshots")
