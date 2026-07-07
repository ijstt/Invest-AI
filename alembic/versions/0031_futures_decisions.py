"""Трек 2 / T2.3: лог торговых решений фьючерсного форка (futures_decisions).

Каждое решение политики (вход/выход) + контекст-признаки на момент решения + исход через горизонт.
Накопительная обучающая выборка для T2.4 (fine-tune малой модели на СВОИХ исходах). Обычная
таблица (не hypertable): объём скромный (решения разрежены — только точки действия), нужны
произвольные апдейты разметки. Идемпотентна по (source, asset_code, interval, ts).

Revision ID: 0031
Revises: 0030
Create Date: 2026-06-19
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS futures_decisions ("
        " id BIGSERIAL PRIMARY KEY,"
        " ts TIMESTAMPTZ NOT NULL,"                  # время решения = время бара
        " asset_code VARCHAR(16) NOT NULL,"          # BR/GOLD/Si (ISS, case-sensitive)
        " interval VARCHAR(8) NOT NULL,"             # 1m/10m/1h
        " contract_secid VARCHAR(32),"               # фронт-контракт на момент решения
        " source VARCHAR(32) NOT NULL,"              # политика: sma_cross/momentum/rsi/…
        " action VARCHAR(8) NOT NULL,"               # buy/sell/hold/close
        " signed_qty INTEGER NOT NULL,"              # знаковая ставка направления
        " price DOUBLE PRECISION NOT NULL,"          # цена решения (close бара)
        " features JSON,"                            # контекст-признаки на момент решения
        " horizon_bars INTEGER,"                     # горизонт разметки (баров)
        " outcome_ts TIMESTAMPTZ,"                   # время бара-исхода
        " outcome_return_pct DOUBLE PRECISION,"      # forward-доходность базиса за горизонт, %
        " outcome_pnl_rub DOUBLE PRECISION,"         # P&L ₽ на 1 контракт, если действовать
        " label VARCHAR(8),"                         # win/loss/flat (NULL — не дозрело)
        " created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_futdecision_point "
        "ON futures_decisions (source, asset_code, interval, ts)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_futdecisions_code_int_ts "
        "ON futures_decisions (asset_code, interval, ts)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS futures_decisions")
