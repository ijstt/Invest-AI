"""Волна 1 (роудмап v2.0): слой рыночных исходов — news_outcomes (E2) и alert_outcomes (E4).

news_outcomes — авто-золото: фактические форвардные доходности (сырые и market-adjusted)
для каждой пары (статья, актив). alert_outcomes — обратная связь алертов: двинулся ли
актив после срабатывания (precision по типам как постоянная метрика).

Идемпотентна: всё под IF NOT EXISTS. Обычные таблицы (не hypertable): объёмы малы,
ключевой доступ — по article_id/alert_id, а не по времени.

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-11
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS news_outcomes (
            id          BIGSERIAL PRIMARY KEY,
            article_id  BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
            asset_id    INTEGER NOT NULL REFERENCES assets(id),
            event_date  DATE NOT NULL,
            base_date   DATE NOT NULL,
            ret_1d      DOUBLE PRECISION,
            ret_3d      DOUBLE PRECISION,
            ret_5d      DOUBLE PRECISION,
            abn_1d      DOUBLE PRECISION,
            abn_3d      DOUBLE PRECISION,
            abn_5d      DOUBLE PRECISION,
            beta        DOUBLE PRECISION,
            relevance   DOUBLE PRECISION,
            computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_news_outcome UNIQUE (article_id, asset_id)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_news_outcomes_article ON news_outcomes (article_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_news_outcomes_asset ON news_outcomes (asset_id)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS alert_outcomes (
            id           BIGSERIAL PRIMARY KEY,
            alert_id     BIGINT NOT NULL UNIQUE REFERENCES alerts(id) ON DELETE CASCADE,
            ticker       VARCHAR(32) NOT NULL,
            base_date    DATE NOT NULL,
            horizon_days INTEGER NOT NULL,
            move_pct     DOUBLE PRECISION NOT NULL,
            abn_move_pct DOUBLE PRECISION,
            hit          BOOLEAN NOT NULL,
            computed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_alert_outcomes_ticker ON alert_outcomes (ticker)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS alert_outcomes")
    op.execute("DROP TABLE IF EXISTS news_outcomes")
