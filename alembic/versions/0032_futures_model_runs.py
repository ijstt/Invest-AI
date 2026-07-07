"""Трек 2 / Фаза B: реестр оценок политик фьючерсного форка (futures_model_runs).

Каждый прогон строгой оценки (walk-forward) пишет сюда OOS-метрики обученной политики во времени:
lift над базой, Sharpe/Sortino/maxDD/profit-factor по взятым сделкам и deflated Sharpe (поправка
на мультитестинг). По реестру выбираем ЧЕМПИОНА (источник+актив+интервал с лучшей робастной
метрикой) и следим за улучшением по мере дозревания данных. Шаблон — continuous-eval Трека 1
(`eval_runs`). Обычная таблица, идемпотентность не требуется (каждый прогон — новая строка-факт).

Revision ID: 0032
Revises: 0031
Create Date: 2026-06-20
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS futures_model_runs ("
        " id BIGSERIAL PRIMARY KEY,"
        " ts TIMESTAMPTZ NOT NULL DEFAULT now(),"     # время прогона оценки
        " source VARCHAR(32) NOT NULL,"               # политика: sma_cross/momentum/…
        " asset_code VARCHAR(16),"                    # NULL = пулинг по всем активам
        " interval VARCHAR(8) NOT NULL,"              # 1m/10m/1h/1d
        " threshold DOUBLE PRECISION NOT NULL,"       # порог P(win) гейта
        " n_folds INTEGER NOT NULL,"                  # число walk-forward фолдов
        " n_samples INTEGER NOT NULL,"                # всего размеченных решений
        " n_taken INTEGER NOT NULL,"                  # взято сделок моделью (OOS)
        " base_win_rate DOUBLE PRECISION,"            # win-rate «брать всё» (OOS)
        " model_win_rate DOUBLE PRECISION,"           # win-rate взятых моделью (OOS)
        " lift DOUBLE PRECISION,"                     # model − base
        " auc DOUBLE PRECISION,"                      # ROC-AUC OOS
        " sharpe DOUBLE PRECISION,"                   # Sharpe доходностей взятых сделок
        " sortino DOUBLE PRECISION,"
        " max_drawdown DOUBLE PRECISION,"             # макс. просадка эквити взятых сделок
        " profit_factor DOUBLE PRECISION,"
        " deflated_sharpe DOUBLE PRECISION,"          # P(SR>0) с поправкой на мультитестинг
        " n_trials INTEGER,"                          # сколько проб учтено в deflated Sharpe
        " is_champion BOOLEAN NOT NULL DEFAULT FALSE,"
        " model_path VARCHAR(256),"
        " note VARCHAR(256),"
        " created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_futmodelruns_key "
        "ON futures_model_runs (source, asset_code, interval, ts)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS futures_model_runs")
