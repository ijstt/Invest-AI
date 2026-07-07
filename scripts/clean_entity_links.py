#!/usr/bin/env python
"""Пересинхронизация связей article↔entity по ИСПРАВЛЕННОМУ матчеру (фикс substring-бага).

Зачем: матчер entity-linking ловил короткое NER-упоминание-аббревиатуру как ПОДСТРОКУ
многословного алиаса («ЕС»/«Си»/«МО» внутри «мобильный телесистема») и ложно привязывал
новости к активам (на MTSS 88 из 89 связей были ложными). Код исправлен (матч по границам
слов), но `geo relink` только ДОБАВЛЯЕТ связи и не удаляет устаревшие. Этот скрипт для
каждой статьи пересчитывает связи текущим (исправленным) матчером и СИНХРОНИЗИРУЕТ таблицу:
удаляет связи, которых матчер больше не даёт, добавляет недостающие, пересчитывает значимость.

Безопасность: работает ТОЛЬКО при доступной морфологии/NER (иначе матчер недопроизводит и
снёс бы легитимные связи — тогда скрипт прерывается). По умолчанию dry-run.

Запуск:
    .venv/bin/python scripts/clean_entity_links.py            # отчёт, без изменений
    .venv/bin/python scripts/clean_entity_links.py --apply    # применить синхронизацию
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from sqlalchemy import delete, select

from geoanalytics import processing
from geoanalytics.nlp import ner
from geoanalytics.nlp.entity_linking import EntityIndex
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import Article, ArticleEntity


def main() -> None:
    ap = argparse.ArgumentParser(description="Синхронизация связей по исправленному матчеру.")
    ap.add_argument("--apply", action="store_true", help="Реально писать в БД (иначе dry-run).")
    ap.add_argument("--limit", type=int, default=100000, help="Сколько статей обработать.")
    args = ap.parse_args()

    # Гард: без морфологии матчер недопроизводит связи → удаление было бы разрушительным.
    if ner.lemmas("Сбербанка") in (None, ["сбербанка"]):
        sys.exit("Морфология (Natasha) недоступна — синхронизация небезопасна, прерываю.")

    removed = added = changed_articles = sig_changed = 0
    with session_scope() as session:
        index = EntityIndex(session)
        asset_cache = processing._load_asset_cache(session)
        articles = list(session.scalars(select(Article).limit(args.limit)))
        for art in articles:
            full_text = f"{art.title}. {art.text or ''}".strip()
            mentions = ner.extract_entities(full_text)
            links = index.match(full_text, [m.normal for m in mentions])
            extra = processing._extra_entity_rows(session, links, full_text, asset_cache)
            new_keys = {(lk.entity_type.value, lk.entity_id) for lk in links}
            new_keys |= {(etype, eid) for etype, eid, _, _ in extra}

            existing = session.execute(
                select(ArticleEntity.id, ArticleEntity.entity_type, ArticleEntity.entity_id)
                .where(ArticleEntity.article_id == art.id)
            ).all()
            stale_ids = [rid for rid, et, eid in existing if (et, eid) not in new_keys]
            existing_keys = {(et, eid) for _, et, eid in existing}

            if stale_ids:
                removed += len(stale_ids)
                changed_articles += 1
                if args.apply:
                    session.execute(delete(ArticleEntity).where(ArticleEntity.id.in_(stale_ids)))

            # Досоздаём недостающие (на случай улучшения матчинга), идемпотентно.
            if args.apply:
                for lk in links:
                    if (lk.entity_type.value, lk.entity_id) not in existing_keys:
                        session.add(ArticleEntity(
                            article_id=art.id, entity_type=lk.entity_type.value,
                            entity_id=lk.entity_id, mention=lk.mention[:256],
                            sentiment=art.sentiment, relevance=lk.relevance))
                        added += 1
                for etype, eid, mention, rel in extra:
                    if (etype, eid) not in existing_keys:
                        session.add(ArticleEntity(
                            article_id=art.id, entity_type=etype, entity_id=eid,
                            mention=mention[:256], sentiment=art.sentiment, relevance=rel))
                        added += 1
                # Значимость зависит от связей — пересчитываем.
                new_sig = processing._compute_significance(
                    art.event_type, art.sentiment_score,
                    [lk.relevance for lk in links], full_text)
                if new_sig != art.significance:
                    art.significance = new_sig
                    sig_changed += 1

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] статей={len(articles)} с устаревшими связями={changed_articles} "
          f"удалено={removed} добавлено={added} значимость_изменена={sig_changed}")
    if not args.apply and removed:
        print("DRY-RUN: запусти с --apply, чтобы применить.")


if __name__ == "__main__":
    main()
