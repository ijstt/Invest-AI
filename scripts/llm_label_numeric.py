#!/usr/bin/env python
"""LLM-разметка числовых фактов для eval F5 numeric extraction (Волна 3).

Qwen — ЭТАЛОН для проверки rule-based экстрактора (nlp/numeric.py), не учитель
дистилляции: статей с числами ~400, модель тут не нужна. QA-формат: по статье
LLM возвращает JSON с тремя полями (null — факта нет). Числа копируются ИЗ
ТЕКСТА без пересчёта единиц (LLM ненадёжно умножает на млрд) — нормализация
на стороне eval-харнеса.

Кандидаты: статьи с триггерами (дивиденд / ключевая ставка / суммы с
множителем) + случайные негативы для замера false positives.

Скрипт идемпотентен и резюмируем (ключ article_id). Выход: data/numeric_gold.jsonl
    {"article_id", "text", "dividend", "key_rate", "deal"}

Запуск:
    python scripts/llm_label_numeric.py [--limit N] [--negatives M]
"""

from __future__ import annotations

import argparse
import json
import random
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

_CANDIDATE_RE = re.compile(
    r"дивиденд|ключев\S+\s+ставк|\d\s*(тыс|млн|млрд|трлн)", re.IGNORECASE
)

_PROMPT = (
    "Ты финансовый аналитик. Извлеки из новости числа, если они ЯВНО указаны:\n"
    "dividend — дивиденд на ОДНУ акцию в рублях;\n"
    "key_rate — новое или действующее значение КЛЮЧЕВОЙ ставки ЦБ в процентах "
    "(диапазон или чужая ставка — null);\n"
    "deal — цена СДЕЛКИ: одна сторона купила/продала/выкупила у другой актив, "
    "компанию или долю ЗА эту сумму. value (число как в тексте), mult "
    "(тыс/млн/млрд/трлн или null), currency (RUB/USD/EUR/CNY). НЕ сделка (null): "
    "выручка, бюджет, налоги, резервы, денежная база, инвестиции, стоимость "
    "проекта, рост/снижение показателя, капитализация. Примеры НЕ сделки: "
    "«30 трлн могут быть вложены в экономику» → null; «мост может стоить "
    "1,1 трлн» → null. Пример сделки: «продал Авто.ру за 35 млрд рублей».\n"
    "Числа бери КАК В ТЕКСТЕ, не пересчитывай единицы. Нет факта — null.\n"
    'Ответь СТРОГО одним JSON без пояснений, формат: {{"dividend": 5.19, '
    '"key_rate": null, "deal": {{"value": 35, "mult": "млрд", "currency": "RUB"}}}}\n'
    "Новость: {text}\nJSON:"
)

_MULTS = ("тыс", "млн", "млрд", "трлн")
_CURRENCIES = ("RUB", "USD", "EUR", "CNY")


def _to_num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", ".").replace(" ", ""))
        except ValueError:
            return None
    return None


def parse_response(raw: str) -> dict | None:
    """Нормализованный ответ LLM; None — не распарсилось."""
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    deal = data.get("deal")
    deal_norm = None
    if isinstance(deal, dict):
        value = _to_num(deal.get("value"))
        mult = deal.get("mult")
        mult = mult.lower().strip(". ") if isinstance(mult, str) else None
        currency = deal.get("currency")
        currency = currency.upper() if isinstance(currency, str) else None
        if value is not None and currency in _CURRENCIES:
            deal_norm = {"value": value,
                         "mult": mult if mult in _MULTS else None,
                         "currency": currency}
    return {
        "dividend": _to_num(data.get("dividend")),
        "key_rate": _to_num(data.get("key_rate")),
        "deal": deal_norm,
    }


def _label_one(text: str, settings, *, max_chars: int = 700) -> dict | None:
    prompt = _PROMPT.format(text=text[:max_chars])
    try:
        resp = httpx.post(
            f"{settings.ollama_host}/api/generate",
            json={
                "model": settings.llm_model,
                "prompt": prompt,
                "stream": False,
                "keep_alive": settings.llm_keep_alive,
                "options": {"num_ctx": 2048, "num_predict": 96, "temperature": 0},
            },
            timeout=180,
        )
        resp.raise_for_status()
        return parse_response(resp.json().get("response", ""))
    except Exception as exc:  # noqa: BLE001 — сеть/таймаут: пропускаем пример
        print(f"  ! ошибка вызова LLM: {exc}")
        return None


def _load_articles(limit: int | None, negatives: int) -> list[dict]:
    """Кандидаты с триггерами + случайные негативы (seed фиксирован)."""
    with session_scope() as session:
        rows = session.execute(
            select(Article.id, Article.title, Article.text)
            .where(Article.text.is_not(None))
            .order_by(Article.published_at)
        ).all()
    pos, neg = [], []
    for article_id, title, body in rows:
        text = _row_text({"title": title, "text": body})
        if not text:
            continue
        item = {"article_id": article_id, "text": text}
        (pos if _CANDIDATE_RE.search(text) else neg).append(item)
    random.Random(42).shuffle(neg)
    out = pos + neg[:negatives]
    print(f"Кандидатов с триггерами: {len(pos)}; негативов добавлено: "
          f"{min(negatives, len(neg))}")
    return out[:limit] if limit else out


def _load_done(out_path: Path) -> set[int]:
    if not out_path.exists():
        return set()
    return {json.loads(line)["article_id"]
            for line in out_path.open(encoding="utf-8") if line.strip()}


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM-разметка numeric (Волна 3).")
    parser.add_argument("--out", default="data/numeric_gold.jsonl")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--negatives", type=int, default=100)
    args = parser.parse_args()

    settings = get_settings()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    articles = _load_articles(args.limit, args.negatives)
    done = _load_done(out_path)
    todo = [a for a in articles if a["article_id"] not in done]
    print(f"Статей всего: {len(articles)}; размечено: {len(done)}; к разметке: {len(todo)}")

    labeled = 0
    t_start = time.time()
    with out_path.open("a", encoding="utf-8") as f:
        for i, a in enumerate(todo, 1):
            parsed = _label_one(a["text"], settings)
            if parsed is None:
                continue
            f.write(json.dumps({**a, **parsed}, ensure_ascii=False) + "\n")
            f.flush()
            labeled += 1
            if i % 20 == 0:
                rate = i / (time.time() - t_start)
                print(f"  {i}/{len(todo)} ({rate:.2f}/с, размечено {labeled})", flush=True)

    counts = {"dividend": 0, "key_rate": 0, "deal": 0, "empty": 0}
    for line in out_path.open(encoding="utf-8"):
        if not line.strip():
            continue
        row = json.loads(line)
        any_fact = False
        for k in ("dividend", "key_rate", "deal"):
            if row.get(k) is not None:
                counts[k] += 1
                any_fact = True
        if not any_fact:
            counts["empty"] += 1
    print(f"Готово. Распределение: {counts}")


if __name__ == "__main__":
    main()
