"""События: Event.article_id + уникальность EventImpact(event_id, asset_id).

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-03
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Идемпотентно: на свежей БД 0001 (Base.metadata.create_all по актуальным моделям)
    # уже создаёт колонку article_id и оба ограничения. На БД, застрявшей в старом
    # состоянии 0001, объекты добавятся здесь. Поэтому всё под guard'ами существования.
    op.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS article_id BIGINT")
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_events_article') THEN
                ALTER TABLE events ADD CONSTRAINT fk_events_article
                    FOREIGN KEY (article_id) REFERENCES articles (id) ON DELETE SET NULL;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_events_article') THEN
                ALTER TABLE events ADD CONSTRAINT uq_events_article UNIQUE (article_id);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_event_impact') THEN
                ALTER TABLE event_impacts ADD CONSTRAINT uq_event_impact UNIQUE (event_id, asset_id);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_constraint("uq_event_impact", "event_impacts", type_="unique")
    op.drop_constraint("uq_events_article", "events", type_="unique")
    op.drop_constraint("fk_events_article", "events", type_="foreignkey")
    op.drop_column("events", "article_id")
