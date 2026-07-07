"""Трек 2 / Фаза B+: барьер-осознанный выход из ОТКРЫТОЙ позиции (дисциплина = метка обучения).

Тройной барьер (López de Prado) — это ИМЕННО та политика выхода, под которую обучена метка
win/loss (первое касание take-profit / stop-loss / времени ±k·σ от входа). Но бумажное исполнение
РАНЬШЕ выходило только по флипу сигнала или закрытию сессии → позиция «висла» далеко за барьером,
под который её оценивали. Это и train/serve mismatch, и та самая «размытая» торговля (держим, пока
сигнал не перевернётся, игнорируя риск/тейк, которые ассумила модель).

Здесь — чистая проверка ТЕХ ЖЕ барьеров для ЖИВОЙ позиции по пути цены с момента входа: выход
дисциплинирован (режем убыток на −down_mult·σ, фиксируем прибыль на +up_mult·σ, тайм-стоп на
horizon баров) и СОГЛАСОВАН с тем, как размечалось обучение. Параметры — те же, что в лейблинге
(`up_mult`/`down_mult`/`horizon`), чтобы не плодить новый переобучаемый гиперпараметр.

Опциональный трейлинг («let profits run») сознательно НЕ включён по умолчанию: его включение
меняет распределение исходов относительно метки → требует согласованного перелейблинга, иначе skew.
Это явный рычаг на будущее (см. memory track2-fork-plan, Tier A).
"""

from __future__ import annotations

from dataclasses import dataclass

# Дефолты барьеров — ТЕ ЖЕ, что в лейблинге `decisions.label_decisions` (симметрично 1.5σ, 12 бар).
UP_MULT = 1.5
DOWN_MULT = 1.5
HORIZON_BARS = 12


@dataclass
class ExitDecision:
    """Решение барьер-выхода: выходить ли и по какой причине (для лога сделки)."""

    should_exit: bool
    reason: str | None = None


def barrier_exit(direction: int, entry_price: float, entry_vol: float,
                 highs_since: list[float], lows_since: list[float], *,
                 up_mult: float = UP_MULT, down_mult: float = DOWN_MULT,
                 horizon: int = HORIZON_BARS) -> ExitDecision:
    """Достигнут ли барьер выхода для открытой позиции (та же дисциплина, что у метки обучения).

    `direction` +1 лонг / −1 шорт. `highs_since`/`lows_since` — путь баров СТРОГО ПОСЛЕ входа и до
    текущего включительно (их длина = число удержанных баров). `entry_vol` — побарная σ на входе
    (как `vol` в `triple_barrier`, доля). Семантика тройного барьера:
      • лонг: верх `entry·(1+up·σ)` = take-profit, низ `entry·(1−down·σ)` = stop-loss;
      • шорт: зеркально (верх = stop-loss, низ = take-profit).
    Пессимизм: если в одном баре задеты ОБА барьера — берём неблагоприятный (стоп). Если барьеры
    не тронуты за `horizon` баров — тайм-стоп. Цена выхода — на стороне вызывающего (текущий бар),
    здесь только ФАКТ и причина (консервативно: не оптимистично «по барьеру в прошлом»)."""
    held = len(highs_since)
    # Без вола/цены барьеры цены не определены — остаётся лишь тайм-стоп.
    if entry_vol > 0 and entry_price > 0 and held:
        up = entry_price * (1 + up_mult * entry_vol)
        dn = entry_price * (1 - down_mult * entry_vol)
        for hi, lo in zip(highs_since, lows_since, strict=False):
            if direction > 0:
                if lo <= dn:                      # стоп проверяем ПЕРВЫМ (пессимизм)
                    return ExitDecision(True, "stop_loss")
                if hi >= up:
                    return ExitDecision(True, "take_profit")
            else:
                if hi >= up:                      # шорт: верхний барьер — это стоп
                    return ExitDecision(True, "stop_loss")
                if lo <= dn:
                    return ExitDecision(True, "take_profit")
    if held >= horizon:
        return ExitDecision(True, "time_stop")
    return ExitDecision(False)
