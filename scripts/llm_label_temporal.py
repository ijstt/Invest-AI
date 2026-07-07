#!/usr/bin/env python
"""LLM-разметка временно́го статуса новостей для F3 temporal anchoring (Волна 3).

Дата публикации ≠ дата события: «отсечка прошла» и «дивиденды будут» — разная
торговая ценность, а event study (E1) должен якориться на дату СОБЫТИЯ. Qwen
размечает каждую статью одним из классов:

    past     — сообщение о свершившемся событии (сделка закрыта, отчёт вышел);
    future   — анонс/решение о будущем событии с конкретикой (заседание назначено,
               дивиденды объявлены, закон вступит в силу);
    forecast — прогноз/ожидание/мнение (аналитики ждут, может вырасти);
    none     — нет конкретного события (обзор, интервью, объяснение, реклама).

Скрипт идемпотентен и резюмируем: ключ article_id, повторный запуск продолжает
с места остановки. Выход: data/temporal_gold.jsonl
    {"article_id", "text", "label"}

Запуск:
    python scripts/llm_label_temporal.py [--limit N]
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
from geoanalytics.nlp.dataset import _row_text  # noqa: E402
from geoanalytics.storage.db import session_scope  # noqa: E402
from geoanalytics.storage.models import Article  # noqa: E402

LABELS = ("past", "future", "forecast", "none")

_PROMPT = (
    "Ты финансовый аналитик. Определи ВРЕМЕННОЙ СТАТУС главного события новости:\n"
    "past — событие УЖЕ ПРОИЗОШЛО (сделка закрыта, отчёт опубликован, цена упала);\n"
    "future — объявлено КОНКРЕТНОЕ БУДУЩЕЕ событие (заседание назначено, дивиденды "
    "утверждены, закон вступит в силу с даты);\n"
    "forecast — ПРОГНОЗ или ожидание без решения (аналитики ждут, может вырасти, "
    "рассматривает возможность);\n"
    "none — конкретного события нет (обзор, интервью, объяснение, статистика без "
    "новости).\n"
    'Ответь СТРОГО одним JSON без пояснений: {{"label": "past"}}\n'
    "Новость: {text}\nJSON:"
)


def parse_response(raw: str) -> str | None:
    """Метка из ответа LLM; None — не распарсилось."""
    m = re.search(r"\{.*?\}", raw, re.S)
    if m:
        try:
            label = str(json.loads(m.group(0)).get("label", "")).lower().strip()
            if label in LABELS:
                return label
        except (json.JSONDecodeError, AttributeError):
            pass
    low = raw.lower()
    # Толерантный фолбэк: первая метка, встреченная в тексте ответа.
    hits = [(low.find(lab), lab) for lab in LABELS if lab in low]
    return min(hits)[1] if hits else None


def _label_one(text: str, settings, *, max_chars: int = 700) -> str | None:
    prompt = _PROMPT.format(text=text[:max_chars])
    try:
        resp = httpx.post(
            f"{settings.ollama_host}/api/generate",
            json={
                "model": settings.llm_model,
                "prompt": prompt,
                "stream": False,
                "keep_alive": settings.llm_keep_alive,
                "options": {"num_ctx": 2048, "num_predict": 16, "temperature": 0},
            },
            timeout=180,
        )
        resp.raise_for_status()
        return parse_response(resp.json().get("response", ""))
    except Exception as exc:  # noqa: BLE001 — сеть/таймаут: пропускаем пример
        print(f"  ! ошибка вызова LLM: {exc}")
        return None


def _load_articles(limit: int | None) -> list[dict]:
    """Статьи с текстом, хронологически (для честного временно́го сплита)."""
    with session_scope() as session:
        rows = session.execute(
            select(Article.id, Article.title, Article.text)
            .where(Article.text.is_not(None))
            .order_by(Article.published_at)
        ).all()
    out = []
    for article_id, title, body in rows:
        text = _row_text({"title": title, "text": body})
        if text:
            out.append({"article_id": article_id, "text": text})
    return out[:limit] if limit else out


def _load_done(out_path: Path) -> set[int]:
    if not out_path.exists():
        return set()
    return {json.loads(line)["article_id"]
            for line in out_path.open(encoding="utf-8") if line.strip()}


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM-разметка temporal (Волна 3).")
    parser.add_argument("--out", default="data/temporal_gold.jsonl")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    settings = get_settings()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    articles = _load_articles(args.limit)
    done = _load_done(out_path)
    todo = [a for a in articles if a["article_id"] not in done]
    print(f"Статей всего: {len(articles)}; размечено: {len(done)}; к разметке: {len(todo)}")

    labeled = 0
    t_start = time.time()
    with out_path.open("a", encoding="utf-8") as f:
        for i, a in enumerate(todo, 1):
            label = _label_one(a["text"], settings)
            if label is None:
                continue
            f.write(json.dumps({**a, "label": label}, ensure_ascii=False) + "\n")
            f.flush()
            labeled += 1
            if i % 20 == 0:
                rate = i / (time.time() - t_start)
                print(f"  {i}/{len(todo)} ({rate:.2f}/с, размечено {labeled})", flush=True)

    dist: dict[str, int] = {}
    for line in out_path.open(encoding="utf-8"):
        if line.strip():
            lab = json.loads(line)["label"]
            dist[lab] = dist.get(lab, 0) + 1
    print(f"Готово. Распределение: {dict(sorted(dist.items()))}")


if __name__ == "__main__":
    main()
