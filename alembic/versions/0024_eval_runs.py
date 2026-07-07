"""Ось I (наблюдаемость): журнал прогонов непрерывной оценки моделей (eval_runs) — I2.

Каждый прогон фиксирует метрику качества модели на дату (напр. precision гейта значимости
против фактической реакции рынка из news_outcomes). Накопленный ряд позволяет ловить ДРЕЙФ
качества во времени и слать алерт при деградации относительно трейлинг-базы. Таблица append-only.

Идемпотентна: CREATE TABLE / INDEX IF NOT EXISTS.

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-16
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS eval_runs ("
        " id BIGSERIAL PRIMARY KEY,"
        " model_name VARCHAR(64) NOT NULL,"          # significance | sentiment | events
        " metric_name VARCHAR(48) NOT NULL,"         # market_precision | market_recall | ...
        " value DOUBLE PRECISION NOT NULL,"          # значение метрики [0..1]
        " n_samples INTEGER NOT NULL,"               # объём оценки
        " window_days INTEGER,"                      # окно данных оценки
        " created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_eval_runs_model_metric_date "
        "ON eval_runs (model_name, metric_name, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS eval_runs")
