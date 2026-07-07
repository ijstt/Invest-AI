"""H2 (Волна 3): календарь событий — таблица calendar_events.

Запланированные события (заседания ЦБ по ставке, дивидендные отсечки,
позже — отчётности из H1): даты известны ЗАРАНЕЕ → проактивные алерты
и опорные даты для event study (E1).

Идемпотентна: IF NOT EXISTS.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-11
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS calendar_events (
            id         BIGSERIAL PRIMARY KEY,
            kind       VARCHAR(32) NOT NULL,
            event_date DATE NOT NULL,
            asset_id   INTEGER REFERENCES assets(id),
            title      VARCHAR(512) NOT NULL,
            source     VARCHAR(32) NOT NULL,
            dedup_key  VARCHAR(128) NOT NULL,
            payload    JSON,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_calendar_dedup UNIQUE (dedup_key)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_calendar_date ON calendar_events (event_date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_calendar_events_kind ON calendar_events (kind)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS calendar_events")
