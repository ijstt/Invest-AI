"""Трек 2 / T2.1: интрадей-свечи фьючерсов по контрактам (futures_candles).

Отдельная от `prices` таблица форка фьючерсного трейдера: минутные/часовые свечи с идентичностью
конкретного контракта (contract_secid + expiry) для реконструкции склейки непрерывного контракта.
Timescale-гипертаблица по ts (объём интрадея велик). Композитный PK (id, ts) — Timescale требует
партиционную колонку в PK (как у prices).

Идемпотентна: CREATE TABLE / INDEX IF NOT EXISTS + create_hypertable(if_not_exists).

Revision ID: 0030
Revises: 0029
Create Date: 2026-06-19
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS futures_candles ("
        " id BIGSERIAL NOT NULL,"
        " ts TIMESTAMPTZ NOT NULL,"
        " asset_code VARCHAR(16) NOT NULL,"          # BR/GOLD/Si (ISS, case-sensitive)
        " contract_secid VARCHAR(32) NOT NULL,"      # BRN6/SiM6…
        " expiry DATE,"                              # LASTTRADEDATE контракта
        " interval VARCHAR(8) NOT NULL,"             # 1m/10m/1h
        " open DOUBLE PRECISION NOT NULL,"
        " high DOUBLE PRECISION NOT NULL,"
        " low DOUBLE PRECISION NOT NULL,"
        " close DOUBLE PRECISION NOT NULL,"
        " volume DOUBLE PRECISION,"
        " created_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
        " PRIMARY KEY (id, ts)"
        ")"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_futcandle_point "
        "ON futures_candles (contract_secid, ts, interval)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_futcandles_code_int_ts "
        "ON futures_candles (asset_code, interval, ts)"
    )
    op.execute(
        "SELECT create_hypertable('futures_candles', 'ts', "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS futures_candles")
