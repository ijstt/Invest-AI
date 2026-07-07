"""H5: фундаментальные метрики эмитентов из отчётов (asset_fundamentals).

Извлечённые из PDF-отчётов метрики (выручка/чистая прибыль/EBITDA/активы/капитал/EPS/дивиденд/
P/E) — в карточку актива. Одна строка на (актив, метрика, период, источник): повторный разбор
того же отчёта идемпотентен. Значение в базовой единице (RUB или коэффициент), масштаб уже
свёрнут (млрд→1e9). `snippet` — фрагмент исходного текста для проверяемости (как article_numbers).

Идемпотентна: CREATE TABLE / INDEX IF NOT EXISTS.

Revision ID: 0025
Revises: 0024
Create Date: 2026-06-16
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS asset_fundamentals ("
        " id BIGSERIAL PRIMARY KEY,"
        " asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,"
        " metric VARCHAR(24) NOT NULL,"              # revenue/net_profit/ebitda/…
        " value DOUBLE PRECISION NOT NULL,"
        " unit VARCHAR(12) NOT NULL,"                # RUB/USD/EUR/CNY | ratio
        " period VARCHAR(16),"                       # 2024 | 2024-H1 | 2024-9M | NULL
        " source VARCHAR(64) NOT NULL DEFAULT 'pdf',"
        " snippet TEXT,"
        " created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_fundamental_asset_metric_period "
        "ON asset_fundamentals (asset_id, metric, COALESCE(period, ''), source)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS asset_fundamentals")
