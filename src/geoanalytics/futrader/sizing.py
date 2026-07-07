"""Трек 2 / Фаза C: сайзинг позиции и риск-контроль.

В T2.4 наивный confidence-сайзинг (размер линейно растёт с P(win)) усиливал убытки на шумной
выборке. Здесь — принципиальный риск-ориентированный сайзинг:
  1. **Vol-targeting**: размер так, чтобы рублёвый риск 1-барного хода ≈ целевая доля эквити
     (`target_risk_pct`). В волатильный момент позиция МЕНЬШЕ — риск стабилен во времени.
  2. **Дробный Kelly с кэпом**: модуляция по уверенности через долю Келли (f*=(p(b+1)−1)/b),
     нормированную к референсной уверенности и ограниченную — без переплеча на слабом эдже.
  3. **Circuit-breaker**: при просадке эквити сверх лимита новые сделки не открываются.

Чистое ядро (без БД): на вход — эквити, цена, побарная σ (доля), спека контракта.
"""

from __future__ import annotations

from geoanalytics.futrader.evaluation import max_drawdown


def contract_risk_rub(price: float, vol_fraction: float, spec) -> float:
    """Рублёвая σ 1-барного хода ОДНОГО контракта: |σ|·(price/tick)·tick_value."""
    if not spec.tick_size:
        return 0.0
    return abs(vol_fraction) * (price / spec.tick_size) * spec.tick_value


def kelly_fraction(p_win: float, payoff_ratio: float = 1.0) -> float:
    """Доля капитала по Келли при вероятности выигрыша p и коэффициенте выплат b (может быть <0)."""
    b = max(1e-9, payoff_ratio)
    return (p_win * (b + 1) - 1) / b


def conviction_weight(p_win: float, *, threshold: float, payoff_ratio: float = 1.0,
                      ref_p: float = 0.70) -> float:
    """Вес уверенности ∈[0,1]: дробь Келли при p, нормированная к Келли при `ref_p`.

    p ниже порога/без эджа → 0; уверенность ≥ ref_p → 1 (насыщение). Заменяет линейный сайзинг
    на вогнутую по эджу шкалу (Келли), не давая шуму у порога раздувать размер."""
    f = kelly_fraction(p_win, payoff_ratio)
    ref = kelly_fraction(ref_p, payoff_ratio)
    if ref <= 0:
        return 1.0 if f > 0 else 0.0
    return max(0.0, min(f / ref, 1.0))


def vol_target_qty(equity: float, price: float, vol_fraction: float, spec, *,
                   target_risk_pct: float, max_qty: int) -> int:
    """Базовый размер по vol-targeting: σ-риск·qty ≈ target_risk_pct·эквити (пол, кэп `max_qty`)."""
    risk = contract_risk_rub(price, vol_fraction, spec)
    if risk <= 0:
        return min(1, max_qty)
    raw = (target_risk_pct / 100.0 * equity) / risk
    return int(min(max(raw, 0.0), max_qty))


def position_size(p_win: float, *, equity: float, price: float, vol_fraction: float, spec,
                  threshold: float = 0.55, target_risk_pct: float = 1.0,
                  payoff_ratio: float = 1.0, max_qty: int = 5) -> int:
    """Итоговое число контрактов: gate(P≥порог) → vol-target база × вес уверенности (Келли), кэп.

    Ниже порога — 0. Иначе vol-target база, промодулированная `conviction_weight`. Если бюджет
    позволяет хотя бы 1 контракт и эдж положителен — минимум 1 (не «зануляем» валидный сигнал)."""
    if p_win < threshold:
        return 0
    base = vol_target_qty(equity, price, vol_fraction, spec,
                          target_risk_pct=target_risk_pct, max_qty=max_qty)
    w = conviction_weight(p_win, threshold=threshold, payoff_ratio=payoff_ratio)
    qty = int(round(base * w))
    if qty < 1 and base >= 1 and w > 0:
        qty = 1
    return min(qty, max_qty)


def drawdown_breached(equity_curve: list[float], *, limit_pct: float) -> bool:
    """Circuit-breaker: текущая просадка эквити-кривой достигла лимита (в %)."""
    if limit_pct <= 0 or len(equity_curve) < 2:
        return False
    return max_drawdown(equity_curve) * 100.0 >= limit_pct


def risk_scale_for_drawdown(drawdown_pct: float, *, max_dd_pct: float) -> float:
    """Плавный де-риск ∈[0,1]: 1 без просадки → 0 у лимита брейкера (линейно по drawdown).

    Брейкер (`drawdown_breached`) — жёсткий стоп НА лимите; здесь — мягкое уменьшение размера
    ЗАРАНЕЕ, по мере роста просадки, чтобы счёт делевериджился до жёсткого стопа, а не скачком."""
    if max_dd_pct <= 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - drawdown_pct / max_dd_pct))


def position_margin(spec, qty: int) -> float:
    """Гарантийное обеспечение позиции: |qty|·initial_margin контракта (₽)."""
    return abs(qty) * (getattr(spec, "initial_margin", 0.0) or 0.0)


def portfolio_margin_used(positions, spec_map: dict) -> float:
    """Суммарная валовая маржа всех открытых позиций счёта (₽) — мера использованного плеча."""
    total = 0.0
    for p in positions:
        spec = spec_map.get(p.asset_code)
        if spec is not None and p.net_qty:
            total += position_margin(spec, p.net_qty)
    return total


def margin_budget_qty(desired_qty: int, *, equity: float, margin_used: float, spec,
                      max_gross_margin_pct: float) -> int:
    """Урезать желаемый размер так, чтобы валовая маржа портфеля не превысила бюджет плеча.

    Бюджет = `max_gross_margin_pct`% эквити. Возвращает максимально допустимое qty (≤ desired),
    учитывая уже занятую маржу `margin_used`. 0 — бюджет исчерпан (новый вход блокируется)."""
    if desired_qty <= 0 or max_gross_margin_pct <= 0:
        return max(0, desired_qty)
    per = getattr(spec, "initial_margin", 0.0) or 0.0
    if per <= 0:
        return desired_qty
    budget = max_gross_margin_pct / 100.0 * equity
    free = budget - margin_used
    if free <= 0:
        return 0
    return max(0, min(desired_qty, int(free // per)))
