"""Трек 2 / Фаза A→C: снимки стакана (L2 market depth) фьючерсов FORTS — futures_orderbook.

ISS отдаёт лишь МГНОВЕННЫЙ снимок стакана (истории нет), поэтому глубину копим ТОЛЬКО вперёд —
лёгкая служба `geo-depth` снимает топ-уровни фронт-контрактов каждые N секунд. Сырьё (bids/asks
JSON) + предрассчитанные скаляры микроструктуры (best/спред/объёмы/дисбаланс) для дешёвых запросов.
Фичи дисбаланса/спреда и depth-aware филлы подключаются в Фазе C, когда накопится история.

Timescale-гипертаблица по ts (частые снимки → большой объём). Композитный PK (id, ts) — Timescale
требует партиционную колонку в PK. Идемпотентна.

Revision ID: 0037
Revises: 0036
Create Date: 2026-06-27
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS futures_orderbook ("
        " id BIGSERIAL NOT NULL,"
        " ts TIMESTAMPTZ NOT NULL,"                  # время снимка (UTC-метка, как свечи)
        " asset_code VARCHAR(16) NOT NULL,"          # BR/GOLD/Si…
        " contract_secid VARCHAR(32) NOT NULL,"      # фронт-контракт BRN6/SiM6…
        " best_bid DOUBLE PRECISION,"
        " best_ask DOUBLE PRECISION,"
        " spread DOUBLE PRECISION,"                  # best_ask − best_bid
        " bid_vol DOUBLE PRECISION,"                 # суммарный объём бидов по снятым уровням
        " ask_vol DOUBLE PRECISION,"                 # суммарный объём асков
        " imbalance DOUBLE PRECISION,"               # (bid_vol−ask_vol)/(bid_vol+ask_vol) ∈ [−1,1]
        " levels INTEGER,"                           # снято уровней с каждой стороны
        " bids JSONB,"                               # [[price, qty], …] top-N
        " asks JSONB,"
        " created_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
        " PRIMARY KEY (id, ts)"
        ")"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_futorderbook_point "
        "ON futures_orderbook (contract_secid, ts)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_futorderbook_code_ts "
        "ON futures_orderbook (asset_code, ts)"
    )
    op.execute(
        "SELECT create_hypertable('futures_orderbook', 'ts', "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS futures_orderbook")
