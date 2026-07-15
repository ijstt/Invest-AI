#!/usr/bin/env python
"""Диагностика бумажного трейдера: анализ причин выходов и поведения в диапазоне.

Запуск:
    .venv/bin/python scripts/diagnose_trader.py [--account demo] [--limit 200]

Выводит:
    1. Распределение причин выходов (time_stop / stop_loss / take_profit / …)
    2. Средний P&L по каждому типу выхода
    3. Гистограмму удержания позиций (часы)
    4. Рекомендации по HORIZON_BARS

Гипотеза: если time_stop > 50% выходов → горизонт барьера слишком мал.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

# Добавим src/ и корень проекта в путь (если запускается из корня проекта)
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "src"))

from geoanalytics.storage.db import session_scope
from geoanalytics.storage.repositories import FuturesPaperRepository


def bar(val: float, max_val: float, width: int = 30) -> str:
    """ASCII progress bar."""
    filled = int(val / max_val * width) if max_val > 0 else 0
    return "█" * filled + "░" * (width - filled)


def fmt_pnl(val: float | None) -> str:
    if val is None:
        return "    —    "
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:,.0f} ₽"


def main() -> None:
    parser = argparse.ArgumentParser(description="Диагностика трейдера")
    parser.add_argument("--account", default="demo", help="Счёт (default: demo)")
    parser.add_argument("--limit", type=int, default=200,
                        help="Максимум сделок для анализа (default: 200)")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  ДИАГНОСТИКА ТРЕЙДЕРА  |  счёт: {args.account}")
    print(f"{'='*60}")

    with session_scope() as session:
        repo = FuturesPaperRepository(session)
        all_trades = repo.recent_trades(args.account, limit=args.limit)

    if not all_trades:
        print("\n⚠  Нет сделок в базе. Запустите geo futures-intraday paper.\n")
        return

    print(f"\nПроанализировано сделок: {len(all_trades)}\n")

    # ── 1. Распределение причин выхода ──
    exit_counts: dict[str, int] = defaultdict(int)
    exit_pnls: dict[str, list[float]] = defaultdict(list)

    # Канонические группы причин (из paper.py / exits.py)
    SESSION_REASONS = {"session", "session_flat"}     # сессионный форс-флэт (SESSION_DISCIPLINE)
    BARRIER_REASONS = {"stop_loss", "take_profit", "time_stop", "barrier_exit"}
    GATE_REASONS    = {"budget", "size", "conviction", "quality_gate", "regime"}

    for t in all_trades:
        reason = t.reason or "unknown"
        # Сохраняем исходную метку для группировки
        exit_counts[reason] += 1
        if t.realized_pnl is not None:
            exit_pnls[reason].append(t.realized_pnl)

    total = sum(exit_counts.values())
    max_cnt = max(exit_counts.values(), default=1)

    print("─" * 60)
    print(f"{'ПРИЧИНА ВЫХОДА':<20} {'COUNT':>6}  {'%':>5}  {'AVG P&L':>12}  {'БАР'}")
    print("─" * 60)

    sorted_reasons = sorted(exit_counts.items(), key=lambda kv: -kv[1])
    for reason, cnt in sorted_reasons:
        pct = cnt / total * 100
        pnls = exit_pnls.get(reason, [])
        avg_pnl = sum(pnls) / len(pnls) if pnls else None
        b = bar(cnt, max_cnt, 22)
        emoji = ""
        if reason == "time_stop":
            emoji = " ⏱" if pct > 50 else ""
        elif reason == "stop_loss":
            emoji = " 🛑"
        elif reason == "take_profit":
            emoji = " ✅"
        print(f"{reason:<20} {cnt:>6}  {pct:>4.1f}%  {fmt_pnl(avg_pnl):>12}  {b}{emoji}")

    print("─" * 60)

    # ── 2. Диагностика ──
    ts_pct = exit_counts.get("time_stop", 0) / total * 100
    sl_pct = exit_counts.get("stop_loss", 0) / total * 100
    tp_pct = exit_counts.get("take_profit", 0) / total * 100
    session_cnt = sum(exit_counts.get(r, 0) for r in SESSION_REASONS)
    gate_cnt    = sum(exit_counts.get(r, 0) for r in GATE_REASONS)
    barrier_cnt = sum(exit_counts.get(r, 0) for r in BARRIER_REASONS)
    session_pct = session_cnt / total * 100
    gate_pct    = gate_cnt / total * 100
    barrier_pct = barrier_cnt / total * 100

    print(f"\n{'─'*60}")
    print("ВЕРДИКТ:")
    print(f"{'─'*60}")
    print(f"\n  Группы выходов:")
    print(f"    SESSION (сессионный форс-флэт) : {session_pct:>5.1f}%  ({session_cnt} сделок)")
    print(f"    GATE    (гейт: бюджет/размер)  : {gate_pct:>5.1f}%  ({gate_cnt} сделок)")
    print(f"    BARRIER (барьеры SL/TP/time)   : {barrier_pct:>5.1f}%  ({barrier_cnt} сделок)")
    print()

    if session_pct > 40:
        print(f"  ⚠  SESSION_DISCIPLINE ДОМИНИРУЕТ ({session_pct:.0f}%)")
        print("     Позиции закрываются принудительно в конце торгового дня.")
        print("     За ~4-6 рабочих часов (без вечерней сессии) движения")
        print("     не успевают реализоваться — эффективный горизонт < 12h.\n")
        print("     ВАРИАНТЫ РЕШЕНИЯ:")
        print("     A) Включить вечернюю сессию: TRADE_EVENING=True")
        print("        (FORTS торгует до 23:50 MSK — +4 часа горизонта)")
        print("     B) Разрешить овернайт для трендовых стратегий")
        print("        (SESSION_DISCIPLINE=False + ночной риск-контроль)")
        print("     C) Укоротить горизонт лейблинга под реальный (~4-6 баров)")
        print("        (перелейблинг + переобучение, но честнее к данным)\n")
    elif ts_pct > 50:
        print(f"  ⚠  TIME-STOP ДОМИНИРУЕТ ({ts_pct:.0f}%)")
        print("     Рекомендация: увеличить HORIZON_BARS с 12 до 24–36\n")
    elif gate_pct > 40:
        print(f"  ⚡  ГЕЙТЫ ДОМИНИРУЮТ ({gate_pct:.0f}%)")
        print("     Много блокировок по budget/size/conviction.")
        print("     Стратегия слишком консервативна — рассмотрите снижение порогов.\n")
    else:
        print(f"  ✓  Баланс выходов приемлем.")
        print(f"     session={session_pct:.0f}%  barrier={barrier_pct:.0f}%  gate={gate_pct:.0f}%\n")

    # ── 3. Анализ прибыльности по типам ──
    if exit_pnls:
        print("─" * 60)
        print("ПРИБЫЛЬНОСТЬ ПО ТИПАМ ВЫХОДА:")
        print("─" * 60)
        for reason, pnls in sorted(exit_pnls.items(), key=lambda kv: -(sum(kv[1]) / len(kv[1]) if kv[1] else 0)):
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]
            avg = sum(pnls) / len(pnls)
            wr = len(wins) / len(pnls) * 100 if pnls else 0
            sign = "+" if avg >= 0 else ""
            print(f"  {reason:<20}  avg={sign}{avg:+,.0f} ₽  win-rate={wr:.0f}%  "
                  f"(W:{len(wins)} L:{len(losses)})")

    # ── 4. Рекомендации по настройке ──
    print(f"\n{'─'*60}")
    print("ТЕКУЩИЕ ПАРАМЕТРЫ БАРЬЕРА (exits.py + labeling.py):")
    print("─" * 60)
    print("  HORIZON_BARS = 12   (баров удержания до тайм-стопа)")
    print("  UP_MULT      = 1.5  (take-profit = entry × (1 + 1.5σ))")
    print("  DOWN_MULT    = 1.5  (stop-loss   = entry × (1 - 1.5σ))")
    print("\n  На 1h ТФ: 12 баров = ~12 часов ≈ 1.5 торговых дня")
    print("  Для дневного тренда нужно 24–48 баров (2–6 торг. дней)\n")

    if ts_pct > 40:
        print("  РЕКОМЕНДУЕМЫЕ НОВЫЕ ЗНАЧЕНИЯ:")
        horizon_rec = 36 if ts_pct > 60 else 24
        print(f"    HORIZON_BARS = {horizon_rec}")
        print("    UP_MULT      = 2.0  (дать больше пространства для профита)")
        print("    DOWN_MULT    = 1.5  (стоп оставить симметричным / чуть уже)")
        print("\n  ⚠  Изменение барьеров ТРЕБУЕТ:")
        print("     1. Перелейблинга futures_decisions (re-label)")
        print("     2. Переобучения политики (train-policy)")
        print("     Без этого будет train/serve mismatch!\n")


if __name__ == "__main__":
    main()
