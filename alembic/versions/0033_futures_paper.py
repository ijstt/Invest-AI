"""Трек 2 / Фаза D (T2.5): бумажный счёт фьючерсного форка (paper-trading).

Замыкает петлю самообучения: champion-политика (из реестра 0032) исполняет сделки на БУМАЖНОМ
счёте через симулятор, исходы копятся, периодическая переоценка промоутит лучшего чемпиона.
`futures_paper_positions` — текущее состояние позиции по (счёт, инструмент, интервал, стратегия);
`futures_paper_trades` — журнал бумажных исполнений (аудит). Идемпотентность не нужна (журнал —
поток фактов; позиция обновляется по ключу).

Revision ID: 0033
Revises: 0032
Create Date: 2026-06-20
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS futures_paper_positions ("
        " id BIGSERIAL PRIMARY KEY,"
        " account VARCHAR(32) NOT NULL,"
        " asset_code VARCHAR(16) NOT NULL,"
        " interval VARCHAR(8) NOT NULL,"
        " source VARCHAR(32) NOT NULL,"             # стратегия (== ключ чемпиона)
        " net_qty INTEGER NOT NULL DEFAULT 0,"      # знаковая открытая позиция
        " avg_price DOUBLE PRECISION,"              # средняя цена входа
        " realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,"
        " last_price DOUBLE PRECISION,"             # последняя котировка (mark-to-market)
        " updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_futpaperpos_key "
        "ON futures_paper_positions (account, asset_code, interval, source)"
    )
    op.execute(
        "CREATE TABLE IF NOT EXISTS futures_paper_trades ("
        " id BIGSERIAL PRIMARY KEY,"
        " ts TIMESTAMPTZ NOT NULL DEFAULT now(),"
        " account VARCHAR(32) NOT NULL,"
        " asset_code VARCHAR(16) NOT NULL,"
        " interval VARCHAR(8) NOT NULL,"
        " source VARCHAR(32) NOT NULL,"
        " action VARCHAR(8) NOT NULL,"              # buy/sell
        " signed_qty INTEGER NOT NULL,"
        " price DOUBLE PRECISION NOT NULL,"
        " p_win DOUBLE PRECISION,"                  # P(win) чемпиона на входе
        " realized_pnl DOUBLE PRECISION,"           # реализованный P&L закрытия (для sell)
        " reason VARCHAR(64),"                      # entry/exit/gate/breaker
        " created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_futpapertrades_key "
        "ON futures_paper_trades (account, asset_code, interval, source, ts)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS futures_paper_trades")
    op.execute("DROP TABLE IF EXISTS futures_paper_positions")
