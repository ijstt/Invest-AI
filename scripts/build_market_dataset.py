#!/usr/bin/env python
"""Сборка рыночного датасета significance v3 (E3, Волна 1 роудмапа v2.0).

Смена учителя: вместо LLM-разметки метка приходит от РЫНКА — фактической
ценовой реакции из news_outcomes (E2). Каждая статья с рыночным исходом:

    impact = max по связанным активам |abn_1d|  (market-adjusted, %)
    label  = "moved"  если impact ≥ порога (1.0%)  иначе  "flat"

Сплит — ВРЕМЕННОЙ (последние eval-frac по published_at → eval): случайный сплит
на рыночных метках подглядывает в будущее (один сюжет в train и eval).
Дедуп near-дублей — до сплита (та же страховка от утечки, что в train_lora).

Запуск:
    python scripts/build_market_dataset.py \
        --train data/significance_market_train.jsonl \
        --eval data/significance_market_eval.jsonl
Дальше:
    python scripts/train_lora.py --task significance --full-finetune --lr 3e-5 \
        --dataset data/significance_market_train.jsonl \
        --output data/adapters/significance-v3-market
    python scripts/eval_market_significance.py --models data/adapters/significance-v3-market
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from sqlalchemy import func, select  # noqa: E402

from geoanalytics.nlp.dataset import (  # noqa: E402
    MARKET_MOVE_THRESHOLD_PCT,
    build_market_significance_records,
    dedup_normalized,
    label_distribution,
    time_split,
    write_jsonl,
)
from geoanalytics.storage.db import session_scope  # noqa: E402
from geoanalytics.storage.models import Article, NewsOutcome  # noqa: E402


def load_rows() -> list[dict]:
    """Статьи с рыночным исходом, хронологически: {title, text, impact}.

    impact — максимум |abn_1d| по связанным активам (abn → fallback на сырой ret,
    если индекс был недоступен при разметке).
    """
    metric = func.max(func.abs(func.coalesce(NewsOutcome.abn_1d, NewsOutcome.ret_1d)))
    with session_scope() as session:
        rows = session.execute(
            select(Article.title, Article.text, metric.label("impact"))
            .join(NewsOutcome, NewsOutcome.article_id == Article.id)
            .group_by(Article.id, Article.title, Article.text, Article.published_at)
            .order_by(Article.published_at)
        ).all()
    return [{"title": t, "text": x, "impact": i} for t, x, i in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description="Рыночный датасет significance v3 (E3).")
    parser.add_argument("--train", default="data/significance_market_train.jsonl")
    parser.add_argument("--eval", dest="eval_path",
                        default="data/significance_market_eval.jsonl")
    parser.add_argument("--threshold", type=float, default=MARKET_MOVE_THRESHOLD_PCT,
                        help="Порог |abn_1d| (%%) для метки moved.")
    parser.add_argument("--eval-frac", type=float, default=0.2,
                        help="Доля последних (по времени) примеров в eval.")
    args = parser.parse_args()

    rows = load_rows()
    records = build_market_significance_records(rows, threshold_pct=args.threshold)
    n_before = len(records)
    records = dedup_normalized(records)  # до сплита — против утечки near-дублей
    train, eval_ = time_split(records, eval_frac=args.eval_frac)

    write_jsonl(train, args.train)
    write_jsonl(eval_, args.eval_path)
    print(f"Статей с исходом: {len(rows)}; примеров после дедупа: "
          f"{len(records)} (-{n_before - len(records)})")
    print(f"Порог moved: |abn_1d| ≥ {args.threshold}%")
    print(f"train: {len(train)} → {args.train}; метки {label_distribution(train)}")
    print(f"eval : {len(eval_)} → {args.eval_path}; метки {label_distribution(eval_)}")
    if len(records) < 1000:
        print("ВНИМАНИЕ: датасет мал (<1000) — обучение возможно, но деплой только "
              "после доказанного улучшения на этом eval; исходы копятся ежедневно (E2).")


if __name__ == "__main__":
    main()
