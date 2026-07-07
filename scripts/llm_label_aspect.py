#!/usr/bin/env python
"""LLM-разметка пар (статья, актив) для F1 aspect-sentiment + F2 saliency (Волна 2).

Б2: у статьи ОДНА тональность, копируемая во все связи статья↔актив — для
мультиактивных новостей («Сбер обыграл ВТБ по марже») это ошибка, отравляющая
neg_spike, EventImpact и B6. Здесь Qwen размечает каждую пару ОТНОСИТЕЛЬНО актива:

    sentiment — тональность новости для инвестора именно в ЭТОТ актив;
    salient   — актив главный объект новости (true) или фоновое упоминание (false).

Обе метки — одним вызовом (JSON-ответ), CPU-генерация долгая. Скрипт идемпотентен
и резюмируем: ключ (article_id, asset_id), повторный запуск продолжает с места
остановки. Выход: data/aspect_gold.jsonl
    {"article_id", "asset_id", "ticker", "aspect", "text", "sentiment", "salient"}

Запуск:
    python scripts/llm_label_aspect.py [--limit N]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from sqlalchemy import select  # noqa: E402

from config.settings import get_settings  # noqa: E402
from geoanalytics.core.types import EntityType  # noqa: E402
from geoanalytics.nlp.dataset import _row_text  # noqa: E402
from geoanalytics.storage.db import session_scope  # noqa: E402
from geoanalytics.storage.models import Article, ArticleEntity, Asset  # noqa: E402

SENTIMENTS = ("positive", "neutral", "negative")

_PROMPT = (
    "Ты опытный финансовый аналитик. Одна новость может затрагивать несколько компаний "
    "ПО-РАЗНОМУ (одной выгодно, другой вредит, третья лишь упомянута).\n"
    "Оцени новость ИМЕННО ОТНОСИТЕЛЬНО компании {aspect}:\n"
    "1) sentiment — тональность для инвестора в {aspect}: positive (выгодно ей), "
    "negative (вредит ей), neutral (без явного эффекта для неё);\n"
    "2) salient — компания {aspect} является главным объектом новости (true) или "
    "лишь фоновым упоминанием/одной из списка (false).\n"
    'Ответь СТРОГО одним JSON без пояснений: {{"sentiment": "...", "salient": true}}\n'
    "Новость: {text}\nJSON:"
)


def parse_response(raw: str) -> tuple[str, bool] | None:
    """(sentiment, salient) из ответа LLM; None — не распарсилось.

    Сначала честный JSON, затем толерантный фолбэк по подстрокам (Qwen изредка
    добавляет хвост или кавычки-ёлочки).
    """
    m = re.search(r"\{.*?\}", raw, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            sent = str(obj.get("sentiment", "")).lower()
            sal = obj.get("salient")
            if sent in SENTIMENTS and isinstance(sal, bool):
                return sent, sal
        except (json.JSONDecodeError, AttributeError):
            pass
    low = raw.lower()
    sent = next((s for s in SENTIMENTS if s in low), None)
    if sent is None:
        return None
    if "true" in low and "false" not in low:
        return sent, True
    if "false" in low and "true" not in low:
        return sent, False
    return None


def _label_one(aspect: str, text: str, settings,
               *, max_chars: int = 600) -> tuple[str, bool] | None:
    prompt = _PROMPT.format(aspect=aspect, text=text[:max_chars])
    try:
        resp = httpx.post(
            f"{settings.ollama_host}/api/generate",
            json={
                "model": settings.llm_model,
                "prompt": prompt,
                "stream": False,
                "keep_alive": settings.llm_keep_alive,
                "options": {"num_ctx": 2048, "num_predict": 32, "temperature": 0},
            },
            timeout=180,
        )
        resp.raise_for_status()
        return parse_response(resp.json().get("response", ""))
    except Exception as exc:  # noqa: BLE001 — сеть/таймаут: пропускаем пример
        print(f"  ! ошибка вызова LLM: {exc}")
        return None


def _load_pairs(limit: int | None) -> list[dict]:
    """Все пары (статья, актив) с текстом и человекочитаемым аспектом, хронологически."""
    with session_scope() as session:
        rows = session.execute(
            select(ArticleEntity.article_id, ArticleEntity.entity_id,
                   Asset.ticker, Asset.name, Article.title, Article.text)
            .join(Article, Article.id == ArticleEntity.article_id)
            .join(Asset, Asset.id == ArticleEntity.entity_id)
            .where(ArticleEntity.entity_type == EntityType.ASSET.value,
                   Asset.kind != "index")
            .order_by(Article.published_at)
        ).all()
    pairs = []
    for article_id, asset_id, ticker, name, title, body in rows:
        text = _row_text({"title": title, "text": body})
        if not text:
            continue
        aspect = f"{name} ({ticker})" if name and name != ticker else ticker
        pairs.append({"article_id": article_id, "asset_id": asset_id,
                      "ticker": ticker, "aspect": aspect, "text": text})
    return pairs[:limit] if limit else pairs


def _load_done(out_path: Path) -> set[tuple[int, int]]:
    if not out_path.exists():
        return set()
    done = set()
    with out_path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                done.add((rec["article_id"], rec["asset_id"]))
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM-разметка aspect/saliency (Волна 2).")
    parser.add_argument("--out", default="data/aspect_gold.jsonl")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    settings = get_settings()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pairs = _load_pairs(args.limit)
    done = _load_done(out_path)
    todo = [p for p in pairs if (p["article_id"], p["asset_id"]) not in done]
    print(f"Пар всего: {len(pairs)}; размечено: {len(done)}; к разметке: {len(todo)}")

    labeled = 0
    t_start = time.time()
    with out_path.open("a", encoding="utf-8") as f:
        for i, p in enumerate(todo, 1):
            parsed = _label_one(p["aspect"], p["text"], settings)
            if parsed is None:
                continue
            sent, salient = parsed
            f.write(json.dumps({**p, "sentiment": sent, "salient": salient},
                               ensure_ascii=False) + "\n")
            f.flush()
            labeled += 1
            if i % 20 == 0:
                rate = i / (time.time() - t_start)
                print(f"  {i}/{len(todo)} ({rate:.2f}/с, размечено {labeled})", flush=True)

    records = [json.loads(x) for x in out_path.open(encoding="utf-8") if x.strip()]
    sent_dist: dict[str, int] = {}
    sal_dist: dict[str, int] = {}
    for r in records:
        sent_dist[r["sentiment"]] = sent_dist.get(r["sentiment"], 0) + 1
        sal_dist[str(r["salient"])] = sal_dist.get(str(r["salient"]), 0) + 1
    print(f"Готово. {len(records)} пар; sentiment {dict(sorted(sent_dist.items()))}; "
          f"salient {dict(sorted(sal_dist.items()))}")


if __name__ == "__main__":
    main()
