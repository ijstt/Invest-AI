#!/usr/bin/env python
"""Бэкфилл `Article.content_hash` и удаление исторических near-duplicate статей (C1).

Зачем: до фикса дедупа (external_id + нормализованный хеш заголовка) одна новость от
разных лент/источников создавала несколько статей и раздувала счётчики neg-spike алертов.
Этот скрипт приводит ИСТОРИЮ к тому состоянию, которое дал бы go-forward дедуп:
1) проставляет content_hash = normalized_hash(title) всем статьям без него;
2) в пределах окна `dedup_window_hours` оставляет самую раннюю статью с данным хешем,
   остальные удаляет (каскадом уходят ArticleEntity/Embedding; Event.article_id → NULL).

По умолчанию dry-run (только отчёт). Удаление — с флагом --apply.

Запуск:
    .venv/bin/python scripts/dedup_articles.py            # отчёт, без изменений
    .venv/bin/python scripts/dedup_articles.py --apply    # бэкфилл + удаление дублей
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from sqlalchemy import delete, select

from config.settings import get_settings
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import Article
from geoanalytics.storage.repositories import normalized_hash


def backfill(apply: bool) -> int:
    """Проставить content_hash статьям без него. Возвращает число обновлённых."""
    n = 0
    with session_scope() as s:
        rows = s.execute(
            select(Article.id, Article.title).where(Article.content_hash.is_(None))
        ).all()
        for aid, title in rows:
            if apply:
                s.execute(
                    Article.__table__.update().where(Article.id == aid)
                    .values(content_hash=normalized_hash(title or ""))
                )
            n += 1
    return n


def find_duplicates(window_hours: int) -> list[int]:
    """ID статей-дублей: в окне уже была более ранняя статья с тем же хешем заголовка.

    Хеш считаем из title на лету (не полагаемся на бэкфилл — работает и в dry-run).
    """
    with session_scope() as s:
        rows = s.execute(
            select(Article.id, Article.title, Article.published_at)
            .where(Article.published_at.isnot(None))
            .order_by(Article.published_at)
        ).all()
    window = timedelta(hours=window_hours)
    last_kept: dict[str, object] = {}
    dups: list[int] = []
    for aid, title, pub in rows:
        h = normalized_hash(title or "")
        prev = last_kept.get(h)
        if prev is not None and (pub - prev) <= window:
            dups.append(aid)
        else:
            last_kept[h] = pub
    return dups


def main() -> None:
    ap = argparse.ArgumentParser(description="Дедуп исторических near-duplicate статей.")
    ap.add_argument("--apply", action="store_true", help="Реально писать в БД (иначе dry-run).")
    ap.add_argument("--window-hours", type=int, default=None,
                    help="Окно дедупа (деф. из настроек dedup_window_hours).")
    args = ap.parse_args()

    window = (args.window_hours if args.window_hours is not None
              else get_settings().dedup_window_hours)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Режим: {mode}; окно дедупа: {window} ч")

    updated = backfill(args.apply)
    suffix = "(записано)" if args.apply else " (будет записано)"
    print(f"content_hash бэкфилл: {updated} статей{suffix}")

    dups = find_duplicates(window)
    print(f"Найдено дублей в окне: {len(dups)}")

    if not dups:
        print("Дублей нет — ничего не делаем.")
        return

    if args.apply:
        with session_scope() as s:
            # Удаляем пачками; каскады чистят ArticleEntity/Embedding, Event.article_id→NULL.
            for i in range(0, len(dups), 500):
                batch = dups[i:i + 500]
                s.execute(delete(Article).where(Article.id.in_(batch)))
        print(f"Удалено {len(dups)} дублей.")
    else:
        print(f"DRY-RUN: удалили бы {len(dups)} статей. Запусти с --apply.")


if __name__ == "__main__":
    main()
