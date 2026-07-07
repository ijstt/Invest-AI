"""Трек 2 / T2.2: симулятор исполнения фьючерсов FORTS (чистое ядро).

Детерминированное, тестируемое без БД/сети: подаём ордера против потока баров → сделки, маржа,
P&L. Фьючерсная модель: позиция держится под гарантийным обеспечением (ГО, `initial_margin`), а
НЕ на полную стоимость контракта; прибыль/убыток считается в рублях через стоимость шага цены
(`tick_value`, ISS STEPPRICE). Поддержаны шорты, плечо (ограничено через ГО), комиссия и
проскальзывание (в шагах против трейдера). Ликвидация — когда эквити падает ниже ГО позиции.

Стратегия здесь НЕ зашита: симулятор лишь исполняет ордера и ведёт счёт — это фундамент под
T2.3 (лог решений) и T2.4 (петлю самообучения). Ограничения v1: исполнение по цене бара
(market — close±проскальзывание; limit — при пересечении лимита баром), без частичных заливок,
без стакана и задержек.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class ContractSpec:
    """Спецификация фьючерсного контракта (с ISS FORTS securities)."""

    secid: str
    tick_size: float          # MINSTEP — минимальный шаг цены (в пунктах котировки)
    tick_value: float         # STEPPRICE — стоимость шага цены, ₽ за шаг на 1 контракт
    initial_margin: float     # INITIALMARGIN — ГО, ₽ на 1 контракт
    fee: float = 0.0          # BUYSELLFEE — комиссия, ₽ на 1 контракт за сделку (одна сторона)

    def pnl_rub(self, price_delta: float, qty: int) -> float:
        """P&L в ₽ от движения цены на `price_delta` пунктов для `qty` контрактов (знаковых)."""
        if self.tick_size <= 0:
            return 0.0
        return (price_delta / self.tick_size) * self.tick_value * qty


def slippage_liquidity_mult(vol_z: float | None, *, k: float = 0.5, cap: float = 3.0) -> float:
    """Множитель проскальзывания от ликвидности: тонкая сессия (низкий vol_z) → хуже исполнение.

    `vol_z` — z-оценка объёма бара (`decisions.extract_features`). При vol_z<0 (объём ниже обычного)
    слипидж растёт линейно `1+k·(−vol_z)`, `cap` ограничивает выбросы. vol_z None/≥0 → 1.0.
    Убирает оптимизм статического слипиджа: на тонком рынке исполнение реалистично дороже."""
    if vol_z is None or vol_z >= 0:
        return 1.0
    return min(cap, 1.0 + k * (-vol_z))


def slippage_ticks_for_qty(qty: int, *, base_ticks: float = 1.0,
                           impact_per_contract: float = 0.5,
                           liquidity_mult: float = 1.0) -> float:
    """Размер-зависимое проскальзывание (шаги): (base + импакт·(|qty|−1))·liquidity_mult.

    Линейная импакт-модель достаточна для одной площадки FORTS (нет стакана/маршрутизации);
    `liquidity_mult` (≥1) повышает слипидж в тонкой сессии (см. `slippage_liquidity_mult`)."""
    return (base_ticks + impact_per_contract * max(0, abs(qty) - 1)) * liquidity_mult


def fill_price(price: float, side: str, *, tick_size: float, slip_ticks: float) -> float:
    """Цена с проскальзыванием ПРОТИВ трейдера: buy дороже, sell дешевле на slip шагов."""
    adj = slip_ticks * tick_size
    return price + adj if side == "buy" else price - adj


@dataclass(frozen=True)
class Order:
    """Заявка. `qty` — положительное число контрактов; сторона задаёт знак."""

    side: str                 # "buy" | "sell"
    qty: int
    kind: str = "market"      # "market" | "limit"
    limit_price: float | None = None

    @property
    def signed_qty(self) -> int:
        return self.qty if self.side == "buy" else -self.qty


@dataclass(frozen=True)
class Fill:
    """Исполненная сделка."""

    ts: datetime
    side: str
    qty: int
    price: float
    fee: float
    slippage_ticks: float
    realized_pnl: float       # ₽, реализовано этой сделкой (на закрытой/перевёрнутой части)


@dataclass
class SimResult:
    """Итог прогона: кривая эквити, сделки и сводные метрики."""

    fills: list[Fill] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    starting_cash: float = 0.0
    final_equity: float = 0.0
    max_drawdown_rub: float = 0.0
    return_pct: float = 0.0
    n_trades: int = 0
    rejected: int = 0
    liquidated: bool = False


class ExecutionSimulator:
    """Маржинальный симулятор фьючерса: `submit` исполняет ордер, `mark` переоценивает по бару.

    Состояние: `cash` (реализованный кошелёк, ₽), `net_qty` (знаковая позиция: + лонг, − шорт),
    `avg_price` (средняя цена позиции). Эквити = cash + нереализованный P&L. Свободная маржа =
    эквити − занятое ГО (|net_qty| × initial_margin). Ордер отклоняется, если ГО результирующей
    позиции превышает доступное эквити (это и ограничивает плечо).
    """

    def __init__(self, spec: ContractSpec, *, starting_cash: float,
                 slippage_ticks: float = 1.0, allow_short: bool = True) -> None:
        self.spec = spec
        self.cash = float(starting_cash)
        self.starting_cash = float(starting_cash)
        self.slippage_ticks = float(slippage_ticks)
        self.allow_short = allow_short
        self.net_qty = 0
        self.avg_price = 0.0
        self.result = SimResult(starting_cash=float(starting_cash))
        self._last_close: float | None = None
        self._peak_equity = float(starting_cash)

    # — внутреннее —

    def _apply_position(self, signed_qty: int, price: float) -> float:
        """Обновить позицию на `signed_qty` по `price`. Вернуть реализованный ₽ (закрытая часть)."""
        old = self.net_qty
        realized = 0.0
        if old == 0 or (old > 0) == (signed_qty > 0):
            # открытие или наращивание в ту же сторону → пересчёт средней
            new_qty = old + signed_qty
            self.avg_price = (
                (self.avg_price * abs(old) + price * abs(signed_qty)) / abs(new_qty)
            )
            self.net_qty = new_qty
        else:
            # встречная сделка: закрываем (частично/полностью), возможен переворот
            closing = min(abs(signed_qty), abs(old))
            realized = self.spec.pnl_rub(price - self.avg_price, math.copysign(closing, old))
            new_qty = old + signed_qty
            self.net_qty = new_qty
            if new_qty == 0:
                self.avg_price = 0.0
            elif (new_qty > 0) != (old > 0):
                # переворот: остаток открыт в новую сторону по цене сделки
                self.avg_price = price
            # частичное закрытие в ту же сторону — средняя не меняется
        return realized

    def _equity(self, mark_price: float) -> float:
        unrealized = self.spec.pnl_rub(mark_price - self.avg_price, self.net_qty)
        return self.cash + unrealized

    def _margin_used(self) -> float:
        return abs(self.net_qty) * self.spec.initial_margin

    # — публичное —

    def submit(self, order: Order, ts: datetime, *, price: float,
               bar_low: float | None = None, bar_high: float | None = None) -> Fill | None:
        """Исполнить ордер по бару (`price` — опорная цена, обычно close). Вернуть Fill либо None.

        Market: цена = price ± проскальзывание (в шагах, против трейдера). Limit: исполняется лишь
        если бар пересёк лимит (нужны `bar_low`/`bar_high`), по цене лимита без проскальзывания.
        Отклоняется (None, `rejected++`), если шорт запрещён или не хватает эквити под ГО.
        """
        if order.qty <= 0:
            return None
        signed = order.signed_qty
        slip = self.slippage_ticks * self.spec.tick_size

        if order.kind == "limit":
            if order.limit_price is None:
                return None
            lo = bar_low if bar_low is not None else price
            hi = bar_high if bar_high is not None else price
            crossed = (order.side == "buy" and lo <= order.limit_price) or \
                      (order.side == "sell" and hi >= order.limit_price)
            if not crossed:
                return None
            fill_price = order.limit_price
            slip_ticks = 0.0
        else:
            fill_price = price + slip if order.side == "buy" else price - slip
            slip_ticks = self.slippage_ticks

        projected_net = self.net_qty + signed
        if not self.allow_short and projected_net < 0:
            self.result.rejected += 1
            return None

        fee = order.qty * self.spec.fee
        # маржа результирующей позиции должна покрываться эквити (за вычетом комиссии).
        projected_margin = abs(projected_net) * self.spec.initial_margin
        if projected_margin > self._equity(fill_price) - fee:
            self.result.rejected += 1
            return None

        realized = self._apply_position(signed, fill_price)
        self.cash += realized - fee
        self.result.realized_pnl += realized
        self.result.fees_paid += fee
        self.result.n_trades += 1
        fill = Fill(ts=ts, side=order.side, qty=order.qty, price=fill_price, fee=fee,
                    slippage_ticks=slip_ticks, realized_pnl=realized)
        self.result.fills.append(fill)
        return fill

    def mark(self, ts: datetime, close: float) -> float:
        """Переоценить позицию по цене закрытия бара. Записать точку эквити. Вернуть эквити.

        Если эквити опустилось ниже занятого ГО — принудительная ликвидация по `close`.
        """
        self._last_close = close
        equity = self._equity(close)
        # ликвидация: эквити ниже ГО открытой позиции → закрыть по рынку.
        if self.net_qty != 0 and equity < self._margin_used():
            realized = self._apply_position(-self.net_qty, close)
            self.cash += realized
            self.result.realized_pnl += realized
            self.result.liquidated = True
            equity = self.cash
        self.result.equity_curve.append((ts, equity))
        self._peak_equity = max(self._peak_equity, equity)
        self.result.max_drawdown_rub = max(self.result.max_drawdown_rub,
                                           self._peak_equity - equity)
        return equity

    def finalize(self) -> SimResult:
        """Закрыть прогон: зафиксировать финальное эквити и доходность."""
        last = self._last_close if self._last_close is not None else self.avg_price
        self.result.final_equity = self._equity(last)
        if self.starting_cash > 0:
            self.result.return_pct = round(
                (self.result.final_equity - self.starting_cash) / self.starting_cash * 100, 4)
        return self.result

    def run(self, bars, strategy=None) -> SimResult:
        """Прогнать по барам. `strategy(bar, sim) -> Order|None` вызывается до переоценки бара.

        `bar` — объект с атрибутами ts/open/high/low/close (например `ContBar`). Без стратегии
        просто переоценивает (для предзагруженной позиции). Возвращает финальный `SimResult`.
        """
        for bar in bars:
            if strategy is not None:
                order = strategy(bar, self)
                if order is not None:
                    self.submit(order, bar.ts, price=bar.close,
                                bar_low=getattr(bar, "low", None),
                                bar_high=getattr(bar, "high", None))
            self.mark(bar.ts, bar.close)
        return self.finalize()
