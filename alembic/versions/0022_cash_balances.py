"""Расширение состава портфеля: денежные/валютные балансы (cash_balances).

Кэш на брокерском счёте (RUB/USD/EUR/CNY…) — НЕ торгуемый актив (нет свечей), поэтому отдельная
таблица балансов, а не строка в portfolio_positions (там инвариант «одна цена-свеча на актив»).
`user_id` NULL — баланс ВЛАДЕЛЬЦА (дашборд/CLI), иначе личный баланс бот-пользователя (как 5c).
Уникальность — одна строка на валюту В РАМКАХ портфеля: UNIQUE(COALESCE(user_id,0), currency)
(тот же инвариант id≥1 у users, что и в 0020).

Идемпотентна: CREATE TABLE / INDEX IF NOT EXISTS.

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-15
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS cash_balances ("
        " id BIGSERIAL PRIMARY KEY,"
        " user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,"
        " currency VARCHAR(8) NOT NULL,"
        " amount NUMERIC(18,2) NOT NULL,"
        " updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_cash_user_ccy "
        "ON cash_balances (COALESCE(user_id, 0), currency)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cash_balances")
