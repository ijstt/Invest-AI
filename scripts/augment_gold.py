#!/usr/bin/env python
"""Догенерация «золота» для РЕДКИХ классов синтетикой от LLM-учителя.

Проблема: у некоторых классов событий (earnings, dividends) в корпусе мало реальных
примеров (~19), и новых взять негде - весь корпус уже размечен. Модель такой класс почти
не учит. Решение - data augmentation: тот же Qwen генерирует НОВЫЕ реалистичные примеры
нужного класса, и ими добивают обучающую выборку.

Это синтетика (несёт стиль учителя), поэтому: примеры помечаются `synthetic: true`, лежат
в ОТДЕЛЬНОМ файле (оригинальное золото не трогаем), объёмы умеренные, температура высокая
ради разнообразия. Скрипт идемпотентен и резюмируем: дозаписывает в jsonl и дедуплицирует
против уже сгенерированного.

Запуск:
    python scripts/augment_gold.py --task events --label earnings --count 120
    python scripts/augment_gold.py --task events --label dividends --count 120
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

# Запуск как `python scripts/augment_gold.py` — корень репо в путь (config + geoanalytics).
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from config.settings import get_settings

# Описание класса для промпта генерации (по задаче → метка → что генерировать).
_GEN_PROMPTS = {
    "events": {
        "earnings": "финансовая отчётность и результаты компаний РФ (выручка, чистая "
                    "прибыль или убыток, EBITDA, рост/падение показателей за квартал/год)",
        "dividends": "дивиденды компаний РФ (рекомендации совета директоров по дивидендам, "
                     "размер выплаты на акцию, дивидендная отсечка, утверждение собранием)",
        "merger": "слияния и поглощения компаний РФ (M&A): покупка доли или контрольного "
                  "пакета, поглощение, объединение компаний, выкуп акций, продажа актива/"
                  "бизнеса, сделка по приобретению конкурента",
    },
}

# Маркеры нумерации/списка в начале строки ответа модели — срезаем при парсинге.
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


def _build_prompt(topic: str, n: int) -> str:
    """Просим модель выдать n РАЗНЫХ примеров, по одному на строку, без нумерации."""
    return (
        f"Ты финансовый журналист. Придумай {n} РАЗНЫХ коротких реалистичных новостей про "
        f"{topic}. Разные компании, периоды и суммы, разные формулировки. Каждая новость - "
        f"одно-два предложения на русском, по ОДНОЙ на строку, без нумерации и кавычек, "
        f"без пояснений.\nНовости:"
    )


def parse_batch(response: str, *, min_len: int = 25) -> list[str]:
    """Чистый парсер ответа: строки → примеры (срез маркеров, отсев коротких). Тестируется."""
    out: list[str] = []
    for raw in response.splitlines():
        line = _BULLET_RE.sub("", raw).strip().strip('"').strip()
        if len(line) >= min_len:
            out.append(line)
    return out


def _generate_batch(topic: str, n: int, settings, *, temperature: float) -> list[str]:
    """Один вызов Ollama: вернуть до n синтетических примеров (или [] при сбое)."""
    try:
        resp = httpx.post(
            f"{settings.ollama_host}/api/generate",
            json={
                "model": settings.llm_model,
                "prompt": _build_prompt(topic, n),
                "stream": False,
                "keep_alive": settings.llm_keep_alive,
                "options": {"num_ctx": 2048, "num_predict": 700, "temperature": temperature},
            },
            timeout=180,
        )
        resp.raise_for_status()
        return parse_batch(resp.json().get("response", ""))
    except Exception as exc:  # noqa: BLE001 — сеть/таймаут: пропускаем батч
        print(f"  ! ошибка вызова LLM: {exc}")
        return []


def _load_done(out_path: Path, label: str) -> set[str]:
    """Уже сгенерированные тексты для этой метки (для дедупа и резюма)."""
    if not out_path.exists():
        return set()
    done: set[str] = set()
    with out_path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                if rec.get("label") == label:
                    done.add(rec["text"])
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description="Синтетическая догенерация золота.")
    parser.add_argument("--task", choices=sorted(_GEN_PROMPTS), default="events")
    parser.add_argument("--label", required=True, help="Метка класса (earnings, dividends).")
    parser.add_argument("--count", type=int, default=120, help="Сколько уникальных набрать.")
    parser.add_argument("--out", default=None, help="JSONL (деф. data/{task}_gold_aug.jsonl).")
    parser.add_argument("--batch", type=int, default=15, help="Сколько просить за один вызов.")
    parser.add_argument("--temperature", type=float, default=0.9, help="Выше → разнообразнее.")
    args = parser.parse_args()

    topics = _GEN_PROMPTS[args.task]
    if args.label not in topics:
        sys.exit(f"Нет промпта для метки {args.label!r} в задаче {args.task!r}. "
                 f"Есть: {sorted(topics)}")

    settings = get_settings()
    out_path = Path(args.out or f"data/{args.task}_gold_aug.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    done = _load_done(out_path, args.label)
    print(f"Метка: {args.label}; уже есть: {len(done)}; цель: {args.count}")

    topic = topics[args.label]
    t_start = time.time()
    with out_path.open("a", encoding="utf-8") as f:
        stale = 0  # подряд батчей без новых уникальных — страховка от зацикливания
        while len(done) < args.count and stale < 8:
            added = 0
            for text in _generate_batch(topic, args.batch, settings, temperature=args.temperature):
                if text in done:
                    continue
                done.add(text)
                f.write(json.dumps({"text": text, "label": args.label, "synthetic": True},
                                   ensure_ascii=False) + "\n")
                f.flush()
                added += 1
                if len(done) >= args.count:
                    break
            stale = 0 if added else stale + 1
            rate = len(done) / max(1e-6, time.time() - t_start)
            print(f"  {len(done)}/{args.count}  (+{added}, {rate:.2f}/с)")

    print(f"Готово: {args.label} — всего {len(done)} в {out_path}")


if __name__ == "__main__":
    main()
