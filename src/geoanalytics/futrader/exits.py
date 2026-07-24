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

# Асимметричные барьеры (2:1 профит-фактор): TP +2.0σ, SL -1.0σ, тайм-стоп 24 бара.
UP_MULT = 2.0
DOWN_MULT = 1.0
HORIZON_BARS = 24
USE_TRAILING_STOP = True
TRAILING_ACTIVATION_MULT = 1.0


@dataclass
class ExitDecision:
    """Решение барьер-выхода: выходить ли и по какой причине (для лога сделки)."""

    should_exit: bool
    reason: str | None = None


def barrier_exit(direction: int, entry_price: float, entry_vol: float,
                 highs_since: list[float], lows_since: list[float], *,
                 up_mult: float = UP_MULT, down_mult: float = DOWN_MULT,
                 horizon: int = HORIZON_BARS,
                 use_trailing: bool = USE_TRAILING_STOP,
                 trailing_activation_mult: float = TRAILING_ACTIVATION_MULT) -> ExitDecision:
    """Достигнут ли барьер выхода для открытой позиции (асимметричный TP/SL + Trailing Stop).

    `direction` +1 лонг / −1 шорт. `highs_since`/`lows_since` — путь баров СТРОГО ПОСЛЕ входа и до
    текущего включительно (их длина = число удержанных баров). `entry_vol` — побарная σ на входе.
    Семантика барьеров:
      • лонг: верх `entry·(1+up·σ)` = take-profit, низ `entry·(1−down·σ)` = stop-loss;
      • шорт: зеркально (верх = stop-loss, низ = take-profit).
      • trailing stop: при нереализованном плюсе ≥ +activation·σ стоп подтягивается за пиком.
    Пессимизм: стоп проверяется первым. За `horizon` баров — тайм-стоп."""
    held = len(highs_since)
    if entry_vol > 0 and entry_price > 0 and held:
        if direction > 0:
            tp = entry_price * (1 + up_mult * entry_vol)
            sl = entry_price * (1 - down_mult * entry_vol)
            peak_price = entry_price
            trailing_active = False

            for hi, lo in zip(highs_since, lows_since, strict=False):
                # 1. Проверка исходного стопа (до активации трейлинга)
                if not trailing_active and lo <= sl:
                    return ExitDecision(True, "stop_loss")
                # 2. Проверка тейк-профита
                if hi >= tp:
                    return ExitDecision(True, "take_profit")
                # 3. Обновление пика и трейлинг-стопа
                if hi > peak_price:
                    peak_price = hi
                if use_trailing and (peak_price - entry_price) / entry_price >= trailing_activation_mult * entry_vol:
                    trail_sl = peak_price * (1 - down_mult * entry_vol)
                    if trail_sl > sl:
                        sl = trail_sl
                        trailing_active = True
                # 4. Проверка подтянутого трейлинг-стопа
                if trailing_active and lo <= sl:
                    return ExitDecision(True, "trailing_stop")
        else:
            tp = entry_price * (1 - up_mult * entry_vol)
            sl = entry_price * (1 + down_mult * entry_vol)
            trough_price = entry_price
            trailing_active = False

            for hi, lo in zip(highs_since, lows_since, strict=False):
                # 1. Проверка исходного стопа (до активации трейлинга)
                if not trailing_active and hi >= sl:
                    return ExitDecision(True, "stop_loss")
                # 2. Проверка тейк-профита (шорт прибылен при падении)
                if lo <= tp:
                    return ExitDecision(True, "take_profit")
                # 3. Обновление минимума и трейлинг-стопа
                if lo < trough_price:
                    trough_price = lo
                if use_trailing and (entry_price - trough_price) / entry_price >= trailing_activation_mult * entry_vol:
                    trail_sl = trough_price * (1 + down_mult * entry_vol)
                    if trail_sl < sl:
                        sl = trail_sl
                        trailing_active = True
                # 4. Проверка подтянутого трейлинг-стопа
                if trailing_active and hi >= sl:
                    return ExitDecision(True, "trailing_stop")

    if held >= horizon:
        return ExitDecision(True, "time_stop")
    return ExitDecision(False)
