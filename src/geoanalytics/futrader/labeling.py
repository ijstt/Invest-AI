"""Трек 2 / Фаза A: triple-barrier лейблинг (López de Prado).

Фикс-горизонт (T2.3) метит сделку по знаку доходности на N-м баре — игнорирует ПУТЬ и торгуемость
(стоп выбило бы раньше). Triple-barrier ставит три барьера от входа: верхний (take-profit), нижний
(stop-loss) и вертикальный (время). Метка — какой барьер задет ПЕРВЫМ: для лонга верх=win/низ=loss,
для шорта зеркально; вертикаль → знак доходности в сторону ставки (или flat у нулевого хода).
Барьеры масштабируются волатильностью входа (±k·σ) — адаптивно к режиму.

Чистое ядро (без БД): на входе highs/lows/closes + бар входа + знак направления. Касание внутри
бара по high/low; при двусмысленности (оба барьера в одном баре) — ПЕССИМИЗМ: сначала проверяем
неблагоприятный барьер (для лонга — нижний), чтобы не переоценивать стратегию.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BarrierOutcome:
    label: str            # win | loss | flat
    touch_idx: int        # бар, где задет барьер (или вертикальный горизонт)
    return_pct: float     # доходность базиса входа→касание (по цене, со знаком цены)
    barrier: str          # up | down | vertical


def round_trip_cost_rub(spec, qty: int, *, slippage_ticks: float = 1.0) -> float:
    """Издержки полного оборота (вход+выход) на |qty| контрактов в ₽.

    На каждую сторону — комиссия `spec.fee` + проскальзывание `slippage_ticks·tick_value`; ×2 за
    круг. Cost-aware метки/исходы вычитают это: мелкий ход, съеденный издержками, = loss, а не win.
    """
    per_contract_side = spec.fee + slippage_ticks * spec.tick_value
    return 2.0 * per_contract_side * abs(qty)


def bar_return_std(closes: list[float], i: int, *, window: int = 20) -> float | None:
    """Ст. отклонение ПОБАРНЫХ проц-доходностей (в долях) по префиксу до бара i. None в прогреве.

    В отличие от `indicators.volatility` (годовая, %), здесь нужна σ на 1 бар как доля цены —
    в этих единицах ставятся барьеры (±k·σ)."""
    if i < window:
        return None
    seg = closes[i - window: i + 1]
    rets = [(seg[k] - seg[k - 1]) / seg[k - 1]
            for k in range(1, len(seg)) if seg[k - 1]]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return var ** 0.5


def triple_barrier(highs: list[float], lows: list[float], closes: list[float], i: int, sign: int,
                   *, horizon: int, up_mult: float, down_mult: float, vol: float,
                   flat_eps: float = 0.0, end_idx: int | None = None) -> BarrierOutcome:
    """Исход сделки от бара i в направлении `sign` (+1 лонг / −1 шорт). Чистая.

    Верхний барьер = entry·(1+up_mult·vol), нижний = entry·(1−down_mult·vol), `vol` — побарная σ
    (доля). Сканируем (i, end]: первое касание решает. up→win при лонге (loss при шорте),
    down→loss при лонге (win при шорте). Без касания (вертикаль) — знак доходности в сторону ставки;
    |ret| < flat_eps·100 → flat. `touch_idx` — глобальный индекс касания.

    `end_idx` (Фаза A, сессионная дисциплина) — жёсткий потолок вертикали: бар форсированного флэта
    до закрытия сессии. Барьер = min(i+horizon, end_idx, n−1) — сделка не переживает закрытие.
    """
    entry = closes[i]
    up = entry * (1 + up_mult * vol)
    dn = entry * (1 - down_mult * vol)
    n = len(closes)
    end = min(i + horizon, n - 1)
    if end_idx is not None:
        end = min(end, end_idx)
    adverse_down = sign > 0      # для лонга неблагоприятен нижний барьер — проверяем его первым
    for j in range(i + 1, end + 1):
        hit_up = highs[j] >= up
        hit_dn = lows[j] <= dn
        order = ("down", "up") if adverse_down else ("up", "down")
        for which in order:
            if which == "up" and hit_up:
                return BarrierOutcome("win" if sign > 0 else "loss", j,
                                      (up / entry - 1) * 100, "up")
            if which == "down" and hit_dn:
                return BarrierOutcome("loss" if sign > 0 else "win", j,
                                      (dn / entry - 1) * 100, "down")
    # Вертикальный барьер: горизонт исчерпан без касания.
    if end <= i:
        return BarrierOutcome("flat", i, 0.0, "vertical")
    ret = (closes[end] / entry - 1) * 100
    signed = ret * sign
    label = "flat" if abs(ret) < flat_eps * 100 else ("win" if signed > 0 else "loss")
    return BarrierOutcome(label, end, ret, "vertical")
