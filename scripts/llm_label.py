#!/usr/bin/env python
"""Разметка обучающего датасета сильным LLM-учителем (M7).

Weak-supervision из правил/базовых моделей упёрся в потолок: дообучать маленькую
модель на её же метках бессмысленно (адаптер воспроизводит ошибки учителя). Здесь —
«серебро» повышаем до «золота»: метки ставит локальный Qwen2.5 (Ollama), который для
русского финтекста заметно сильнее rubert-base/лексикона. На полученном золоте уже
имеет смысл дообучать LoRA (`scripts/train_lora.py`).

Скрипт идемпотентен и резюмируем: пишет JSONL инкрементально и при повторном запуске
пропускает уже размеченные тексты — CPU-генерация Qwen долгая (~1.5 с/пример), процесс
можно прерывать и продолжать.

Запуск:
    python scripts/llm_label.py --task sentiment --out data/sentiment_gold.jsonl
    python scripts/llm_label.py --task sentiment --limit 500   # ограничить объём
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

# Запуск как `python scripts/llm_label.py` — корень репо в путь (пакеты config + geoanalytics).
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from sqlalchemy import select

from config.settings import get_settings
from geoanalytics.nlp.dataset import _row_text, dedup, label_distribution
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import Article

# Допустимые метки на задачу (для строгого парсинга ответа LLM).
TASK_LABELS = {
    "sentiment": ["positive", "neutral", "negative"],
    "significance": ["low", "medium", "high"],
    # Классы соответствуют EventType (core/types.py). Без news/market — это типы источников,
    # а не категории новостного события.
    "events": ["sanctions", "dividends", "merger", "regulation", "earnings",
               "macro", "geopolitics", "other", "noise"],
}

_PROMPTS = {
    "sentiment": (
        "Ты опытный финансовый аналитик. Оцени тональность новости С ТОЧКИ ЗРЕНИЯ "
        "ИНВЕСТОРА в упомянутый актив/рынок: позитив (рост, прибыль, улучшение), "
        "негатив (падение, убытки, санкции, риски) или нейтрально (факт без явного "
        "влияния, рутина, смешанный эффект). Ответь СТРОГО одним словом на английском: "
        "positive, neutral или negative.\nНовость: {text}\nТональность:"
    ),
    # Дистилляция значимости (Ф7): тот же приём, что и для sentiment. Серебро от формулы
    # (build_significance_records) замкнуто на саму формулу — модель не может её обогнать.
    # Здесь золото ставит Qwen: оценивает ВАЖНОСТЬ новости для инвестора в рынок РФ.
    "significance": (
        "Ты опытный финансовый аналитик. Оцени ВАЖНОСТЬ новости ДЛЯ ИНВЕСТОРА в рынок "
        "РФ — насколько она способна повлиять на котировки/решения:\n"
        "- high: санкции, решения по ключевой ставке ЦБ, крупные движения цены, "
        "отчётность и прибыль компаний, слияния/поглощения, геополитика, регуляторика "
        "с эффектом на рынок;\n"
        "- low: спорт, ДТП и происшествия, культура, светская и бытовая хроника, "
        "реклама — то, что инвестору неинтересно;\n"
        "- medium: рыночный фон без явного сильного эффекта.\n"
        "Ответь СТРОГО одним словом на английском: low, medium или high.\n"
        "Новость: {text}\nВажность:"
    ),
    # Дистилляция типа события (кандидат после significance): правила в classify.py ловят
    # не всё, LoRA проигрывала. Здесь золото ставит Qwen, выбирая ОДНУ категорию события.
    "events": (
        "Ты опытный финансовый аналитик. Определи ТИП новости для рынка РФ, выбрав ровно "
        "одну категорию:\n"
        "- sanctions: санкции, эмбарго, чёрные списки, ограничения поставок;\n"
        "- dividends: дивиденды, выплаты акционерам, дивидендная отсечка;\n"
        "- merger: слияния и поглощения (M&A), покупка доли, выкуп акций;\n"
        "- earnings: отчётность компании, выручка, прибыль, убыток, финрезультаты;\n"
        "- regulation: регулирование, законы, требования ЦБ, лицензии, налоги;\n"
        "- macro: макроэкономика - ключевая ставка, инфляция, ВВП, курс рубля, нефть, бюджет;\n"
        "- geopolitics: геополитика - переговоры, саммиты, конфликты, дипломатия;\n"
        "- noise: спорт, происшествия и ДТП, культура, светская хроника - шум, нерелевантный;\n"
        "- other: всё остальное, что не подходит под категории выше.\n"
        "Ответь СТРОГО одним словом на английском из списка.\n"
        "Новость: {text}\nКатегория:"
    ),
}


def _parse_label(response: str, allowed: list[str]) -> str | None:
    """Берёт первую встретившуюся допустимую метку из ответа LLM (регистронезависимо)."""
    low = response.lower()
    best: tuple[int, str] | None = None
    for lab in allowed:
        pos = low.find(lab)
        if pos != -1 and (best is None or pos < best[0]):
            best = (pos, lab)
    return best[1] if best else None


def _label_one(text: str, task: str, settings, *, max_chars: int) -> str | None:
    """Один вызов Ollama. Короткий num_predict — нужна только метка, не объяснение."""
    prompt = _PROMPTS[task].format(text=text[:max_chars])
    try:
        resp = httpx.post(
            f"{settings.ollama_host}/api/generate",
            json={
                "model": settings.llm_model,
                "prompt": prompt,
                "stream": False,
                "keep_alive": settings.llm_keep_alive,
                "options": {"num_ctx": 2048, "num_predict": 6, "temperature": 0},
            },
            timeout=120,
        )
        resp.raise_for_status()
        return _parse_label(resp.json().get("response", ""), TASK_LABELS[task])
    except Exception as exc:  # noqa: BLE001 — сеть/таймаут: пропускаем пример
        print(f"  ! ошибка вызова LLM: {exc}")
        return None


def _load_texts(limit: int | None) -> list[str]:
    """Уникальные тексты новостей (title+body, как в обучающем датасете)."""
    with session_scope() as session:
        rows = session.execute(select(Article.title, Article.text)).all()
    records = [{"text": _row_text({"title": t, "text": b})} for t, b in rows]
    records = [r for r in dedup(records) if r["text"]]
    texts = [r["text"] for r in records]
    return texts[:limit] if limit else texts


def _load_done(out_path: Path) -> set[str]:
    """Уже размеченные тексты (для резюма)."""
    if not out_path.exists():
        return set()
    done: set[str] = set()
    with out_path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                done.add(json.loads(line)["text"])
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM-разметка датасета (M7).")
    parser.add_argument("--task", choices=sorted(_PROMPTS), default="sentiment")
    parser.add_argument("--out", default=None,
                        help="Файл JSONL (по умолчанию data/{task}_gold.jsonl).")
    parser.add_argument("--limit", type=int, default=None, help="Ограничить число примеров.")
    parser.add_argument("--max-chars", type=int, default=600, help="Обрезка текста в промпте.")
    args = parser.parse_args()

    settings = get_settings()
    # Дефолтный путь зависит от задачи, чтобы не перезаписать золото другой задачи.
    out_path = Path(args.out or f"data/{args.task}_gold.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    texts = _load_texts(args.limit)
    done = _load_done(out_path)
    todo = [t for t in texts if t not in done]
    print(f"Задача: {args.task}; всего текстов: {len(texts)}; уже размечено: {len(done)}; "
          f"к разметке: {len(todo)}")

    labeled = 0
    t_start = time.time()
    with out_path.open("a", encoding="utf-8") as f:
        for i, text in enumerate(todo, 1):
            label = _label_one(text, args.task, settings, max_chars=args.max_chars)
            if label is None:
                continue
            f.write(json.dumps({"text": text, "label": label}, ensure_ascii=False) + "\n")
            f.flush()
            labeled += 1
            if i % 25 == 0:
                rate = i / (time.time() - t_start)
                print(f"  {i}/{len(todo)}  ({rate:.2f}/с, размечено {labeled})")

    # Итоговое распределение по всему файлу.
    all_records = [json.loads(line) for line in out_path.open(encoding="utf-8") if line.strip()]
    print(f"Готово. В файле {len(all_records)} примеров → {label_distribution(all_records)}")


if __name__ == "__main__":
    main()
