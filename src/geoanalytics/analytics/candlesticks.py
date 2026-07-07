"""Свечные паттерны Нисона (дневной таймфрейм) — детекторы и торговый сигнал.

Формализация классики из «Японских свечей» (data/library): каждый паттерн —
детерминированная функция геометрии свечи (тело/тени/диапазон), КОНТЕКСТ ТРЕНДА
обязателен (Нисон: разворотный паттерн без тренда — просто свеча). Молот и
повешенный геометрически одинаковы — различает их только предшествующий тренд;
то же для падающей звезды и перевёрнутого молота.

Книга даёт гипотезы — walk-forward бэктест (B5) отсеивает: стратегия `candles`
зарегистрирована в backtest.OHLC_STRATEGIES, сетка hold/trend в DEFAULT_GRIDS.

Все функции чистые (списки OHLC одинаковой длины → список bool по барам,
паттерн «срабатывает» на баре завершения). Пороги — доли диапазона свечи,
устойчивее абсолютных значений и таймфрейм-агностичны.
"""

from __future__ import annotations

from dataclasses import dataclass

# Пороги геометрии (доли диапазона h-l, если не сказано иное).
_DOJI_BODY = 0.1          # тело доджи ≤ 10% диапазона
_SHADOW_LONG = 0.6        # «длинная» тень ≥ 60% диапазона (молот/звезда)
_SHADOW_SHORT = 0.15      # «короткая» противоположная тень ≤ 15%
_LONG_BODY = 0.6          # «длинное» тело ≥ 60% диапазона (звёзды, харами)
_STAR_BODY = 0.3          # тело звезды ≤ 30% тела первой свечи
_HARAMI_BODY = 0.6        # тело харами < 60% тела предыдущей свечи


def _body(o: float, c: float) -> float:
    return abs(c - o)


def _range(h: float, l: float) -> float:  # noqa: E741 — l это low, локальная конвенция OHLC
    return h - l


def _upper(o: float, h: float, c: float) -> float:
    return h - max(o, c)


def _lower(o: float, l: float, c: float) -> float:  # noqa: E741
    return min(o, c) - l


def sma_series(values: list[float], window: int) -> list[float | None]:
    """Скользящее среднее по барам (None на прогреве). Чистая, O(n)."""
    out: list[float | None] = [None] * len(values)
    acc = 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= window:
            acc -= values[i - window]
        if i >= window - 1:
            out[i] = acc / window
    return out


def trend_context(closes: list[float], window: int = 10) -> list[int]:
    """Контекст тренда на бар: -1 нисходящий, +1 восходящий, 0 неизвестен.

    Тренд оценивается по ПРЕДЫДУЩЕМУ бару (closes[i-1] против его SMA) — паттерн
    бара i разворачивает тренд, сложившийся ДО него; заглядывания нет.
    """
    ma = sma_series(closes, window)
    out = [0] * len(closes)
    for i in range(1, len(closes)):
        prev_ma = ma[i - 1]
        if prev_ma is None:
            continue
        if closes[i - 1] < prev_ma:
            out[i] = -1
        elif closes[i - 1] > prev_ma:
            out[i] = 1
    return out


# --------------------------------------------------------------------------- #
# Геометрия одиночных свечей.
# --------------------------------------------------------------------------- #
def doji(opens, highs, lows, closes) -> list[bool]:
    """Доджи: тело ≤ 10% диапазона (нерешительность; сигнал только в контексте)."""
    out = []
    for o, h, l, c in zip(opens, highs, lows, closes, strict=True):  # noqa: E741
        rng = _range(h, l)
        out.append(rng > 0 and _body(o, c) <= _DOJI_BODY * rng)
    return out


def hammer_shape(opens, highs, lows, closes) -> list[bool]:
    """Геометрия молота/повешенного: длинная нижняя тень, малая верхняя.

    В нисходящем тренде это «молот» (бычий), в восходящем — «повешенный»
    (медвежий). Контекст накладывается в detect_patterns/candle_signals.
    """
    out = []
    for o, h, l, c in zip(opens, highs, lows, closes, strict=True):  # noqa: E741
        rng = _range(h, l)
        out.append(rng > 0
                   and _lower(o, l, c) >= _SHADOW_LONG * rng
                   and _upper(o, h, c) <= _SHADOW_SHORT * rng)
    return out


def shooting_star_shape(opens, highs, lows, closes) -> list[bool]:
    """Геометрия падающей звезды/перевёрнутого молота: длинная верхняя тень."""
    out = []
    for o, h, l, c in zip(opens, highs, lows, closes, strict=True):  # noqa: E741
        rng = _range(h, l)
        out.append(rng > 0
                   and _upper(o, h, c) >= _SHADOW_LONG * rng
                   and _lower(o, l, c) <= _SHADOW_SHORT * rng)
    return out


# --------------------------------------------------------------------------- #
# Двух- и трёхсвечные паттерны.
# --------------------------------------------------------------------------- #
def bullish_engulfing(opens, highs, lows, closes) -> list[bool]:
    """Бычье поглощение: тело белой свечи накрывает тело предыдущей чёрной."""
    out = [False] * len(closes)
    for i in range(1, len(closes)):
        prev_bear = closes[i - 1] < opens[i - 1]
        cur_bull = closes[i] > opens[i]
        out[i] = (prev_bear and cur_bull
                  and opens[i] <= closes[i - 1] and closes[i] >= opens[i - 1]
                  and _body(opens[i], closes[i]) > _body(opens[i - 1], closes[i - 1]))
    return out


def bearish_engulfing(opens, highs, lows, closes) -> list[bool]:
    """Медвежье поглощение: тело чёрной свечи накрывает тело предыдущей белой."""
    out = [False] * len(closes)
    for i in range(1, len(closes)):
        prev_bull = closes[i - 1] > opens[i - 1]
        cur_bear = closes[i] < opens[i]
        out[i] = (prev_bull and cur_bear
                  and opens[i] >= closes[i - 1] and closes[i] <= opens[i - 1]
                  and _body(opens[i], closes[i]) > _body(opens[i - 1], closes[i - 1]))
    return out


def bullish_harami(opens, highs, lows, closes) -> list[bool]:
    """Бычье харами: малое тело внутри тела предыдущей длинной чёрной свечи."""
    out = [False] * len(closes)
    for i in range(1, len(closes)):
        rng_prev = _range(highs[i - 1], lows[i - 1])
        prev_bear_long = (closes[i - 1] < opens[i - 1] and rng_prev > 0
                          and _body(opens[i - 1], closes[i - 1]) >= _LONG_BODY * rng_prev)
        body_inside = (max(opens[i], closes[i]) <= opens[i - 1]
                       and min(opens[i], closes[i]) >= closes[i - 1])
        small = _body(opens[i], closes[i]) < _HARAMI_BODY * _body(opens[i - 1], closes[i - 1])
        out[i] = prev_bear_long and body_inside and small
    return out


def bearish_harami(opens, highs, lows, closes) -> list[bool]:
    """Медвежье харами: малое тело внутри тела предыдущей длинной белой свечи."""
    out = [False] * len(closes)
    for i in range(1, len(closes)):
        rng_prev = _range(highs[i - 1], lows[i - 1])
        prev_bull_long = (closes[i - 1] > opens[i - 1] and rng_prev > 0
                          and _body(opens[i - 1], closes[i - 1]) >= _LONG_BODY * rng_prev)
        body_inside = (max(opens[i], closes[i]) <= closes[i - 1]
                       and min(opens[i], closes[i]) >= opens[i - 1])
        small = _body(opens[i], closes[i]) < _HARAMI_BODY * _body(opens[i - 1], closes[i - 1])
        out[i] = prev_bull_long and body_inside and small
    return out


def morning_star(opens, highs, lows, closes) -> list[bool]:
    """Утренняя звезда: длинная чёрная → звезда (малое тело) → белая в тело первой.

    Классика требует гэп у звезды; на дневном рынке РФ гэпы редки — Нисон
    допускает послабление, требуем только малое тело и закрытие третьей свечи
    выше середины тела первой.
    """
    out = [False] * len(closes)
    for i in range(2, len(closes)):
        o1, c1 = opens[i - 2], closes[i - 2]
        rng1 = _range(highs[i - 2], lows[i - 2])
        first_bear_long = c1 < o1 and rng1 > 0 and _body(o1, c1) >= _LONG_BODY * rng1
        star_small = _body(opens[i - 1], closes[i - 1]) <= _STAR_BODY * _body(o1, c1)
        third_bull = closes[i] > opens[i]
        into_first = closes[i] >= (o1 + c1) / 2
        out[i] = first_bear_long and star_small and third_bull and into_first
    return out


def evening_star(opens, highs, lows, closes) -> list[bool]:
    """Вечерняя звезда: длинная белая → звезда → чёрная в тело первой."""
    out = [False] * len(closes)
    for i in range(2, len(closes)):
        o1, c1 = opens[i - 2], closes[i - 2]
        rng1 = _range(highs[i - 2], lows[i - 2])
        first_bull_long = c1 > o1 and rng1 > 0 and _body(o1, c1) >= _LONG_BODY * rng1
        star_small = _body(opens[i - 1], closes[i - 1]) <= _STAR_BODY * _body(o1, c1)
        third_bear = closes[i] < opens[i]
        into_first = closes[i] <= (o1 + c1) / 2
        out[i] = first_bull_long and star_small and third_bear and into_first
    return out


# --------------------------------------------------------------------------- #
# Сводный детектор (контекст тренда → имена Нисона) и торговый сигнал.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PatternHit:
    """Найденный паттерн: бар, имя по Нисону, направление (+1 бычий / -1 медвежий)."""

    index: int
    name: str
    direction: int


def detect_patterns(opens, highs, lows, closes, *,
                    trend: int = 10) -> list[PatternHit]:
    """Все паттерны по ряду с контекстом тренда (имена по Нисону).

    Молот требует нисходящего тренда, повешенный — восходящего (геометрия одна);
    поглощение/харами/звёзды — тренда против своего направления. Доджи —
    информационный (direction 0), в торговый сигнал не входит.
    """
    ctx = trend_context(closes, trend)
    hits: list[PatternHit] = []
    series = {
        "молот": (hammer_shape(opens, highs, lows, closes), -1, +1),
        "повешенный": (hammer_shape(opens, highs, lows, closes), +1, -1),
        "перевёрнутый молот": (shooting_star_shape(opens, highs, lows, closes), -1, +1),
        "падающая звезда": (shooting_star_shape(opens, highs, lows, closes), +1, -1),
        "бычье поглощение": (bullish_engulfing(opens, highs, lows, closes), -1, +1),
        "медвежье поглощение": (bearish_engulfing(opens, highs, lows, closes), +1, -1),
        "бычье харами": (bullish_harami(opens, highs, lows, closes), -1, +1),
        "медвежье харами": (bearish_harami(opens, highs, lows, closes), +1, -1),
        "утренняя звезда": (morning_star(opens, highs, lows, closes), -1, +1),
        "вечерняя звезда": (evening_star(opens, highs, lows, closes), +1, -1),
    }
    for name, (flags, need_ctx, direction) in series.items():
        for i, flag in enumerate(flags):
            if flag and ctx[i] == need_ctx:
                hits.append(PatternHit(index=i, name=name, direction=direction))
    for i, flag in enumerate(doji(opens, highs, lows, closes)):
        if flag:
            hits.append(PatternHit(index=i, name="доджи", direction=0))
    hits.sort(key=lambda h: h.index)
    return hits


# Слабые одиночные сигналы, исключённые из торговли (информационные в CLI):
# перевёрнутый молот требует подтверждения следующей свечой (Нисон), доджи —
# нерешительность. В сигнал входят подтверждённые разворотные паттерны.
_TRADE_EXCLUDED = {"перевёрнутый молот", "доджи"}


def candle_signals(closes: list[float], *, opens: list[float],
                   highs: list[float], lows: list[float],
                   hold: int = 10, trend: int = 10) -> list[int]:
    """Сигнал 0/1 по свечным паттернам: лонг от бычьего разворота.

    Вход — бычий паттерн в нисходящем тренде; выход — медвежий паттерн или
    `hold` баров без подтверждения (повторный бычий паттерн обновляет таймер).
    Сигнатура совместима с PRICE_STRATEGIES (`fn(closes, **params)`): OHLC
    передаются keyword-параметрами, DB-раннер подставляет их через partial.
    """
    hits = detect_patterns(opens, highs, lows, closes, trend=trend)
    bull = [False] * len(closes)
    bear = [False] * len(closes)
    for h in hits:
        if h.name in _TRADE_EXCLUDED:
            continue
        if h.direction > 0:
            bull[h.index] = True
        elif h.direction < 0:
            bear[h.index] = True

    signals = [0] * len(closes)
    pos, held_bars = 0, 0
    for i in range(len(closes)):
        if pos == 0:
            if bull[i]:
                pos, held_bars = 1, 0
        else:
            held_bars += 1
            if bull[i]:
                held_bars = 0
            if bear[i] or held_bars >= hold:
                pos = 0
        signals[i] = pos
    return signals


def candles_directional(closes: list[float], *, opens: list[float] | None = None,
                        highs: list[float] | None = None, lows: list[float] | None = None,
                        hold: int = 10, trend: int = 10) -> list[int]:
    """ДВУСТОРОННИЙ свечной сигнал (−1/0/+1) по подтверждённым разворотным паттернам Нисона.

    ЛОНГ — бычий разворот (молот/бычье поглощение/харами/утренняя звезда) в нисходящем тренде;
    ШОРТ — медвежий разворот в восходящем (зеркально). Держим до ПРОТИВОПОЛОЖНОГО паттерна (тогда
    флип) или `hold` баров без подтверждения (повтор того же направления обновляет таймер). Доджи/
    слабые исключены (`_TRADE_EXCLUDED`). OHLC по умолчанию = closes (вырожд. ряд → нет паттернов,
    все 0) — чтобы сигнатура была совместима с диспетчем `fn(closes)`. На том же ТФ, что торгуем."""
    n = len(closes)
    opens = opens if opens is not None else closes
    highs = highs if highs is not None else closes
    lows = lows if lows is not None else closes
    hits = detect_patterns(opens, highs, lows, closes, trend=trend)
    bull = [False] * n
    bear = [False] * n
    for h in hits:
        if h.name in _TRADE_EXCLUDED:
            continue
        if h.direction > 0:
            bull[h.index] = True
        elif h.direction < 0:
            bear[h.index] = True

    out = [0] * n
    pos, held = 0, 0
    for i in range(n):
        if pos == 0:
            if bull[i]:
                pos, held = 1, 0
            elif bear[i]:
                pos, held = -1, 0
        else:
            held += 1
            opp = bear[i] if pos > 0 else bull[i]
            same = bull[i] if pos > 0 else bear[i]
            if opp:                              # противоположный разворот → флип стороны
                pos, held = -pos, 0
            elif same:                           # подтверждение направления → сброс таймера
                held = 0
            elif held >= hold:                   # истёк без подтверждения → флэт
                pos, held = 0, 0
        out[i] = pos
    return out


# --------------------------------------------------------------------------- #
# DB-раннер: паттерны по истории актива (для CLI `geo candles`).
# --------------------------------------------------------------------------- #
def patterns_for_asset(ticker: str, *, days: int = 90,
                       trend: int = 10) -> list[tuple] | None:
    """Свечные паттерны актива за последние `days` баров: [(дата, PatternHit)].

    None — актив не найден. Детекция идёт по ВСЕЙ истории (тренд-контексту нужен
    прогрев), наружу отдаются только хиты свежего окна.
    """
    from sqlalchemy import asc, select

    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset, Price

    with session_scope() as session:
        asset = session.scalars(
            select(Asset).where(Asset.ticker == ticker.upper())
        ).first()
        if asset is None:
            return None
        rows = session.execute(
            select(Price.ts, Price.open, Price.high, Price.low, Price.close)
            .where(Price.asset_id == asset.id, Price.interval == "1d")
            .order_by(asc(Price.ts))
        ).all()
    if not rows:
        return []
    dates = [ts.date() for ts, *_ in rows]
    closes = [float(c) for *_, c in rows]
    opens = [float(o) if o is not None else c
             for (_ts, o, _h, _l, _c), c in zip(rows, closes, strict=True)]
    highs = [float(h) if h is not None else c
             for (_ts, _o, h, _l, _c), c in zip(rows, closes, strict=True)]
    lows = [float(lo) if lo is not None else c
            for (_ts, _o, _h, lo, _c), c in zip(rows, closes, strict=True)]
    hits = detect_patterns(opens, highs, lows, closes, trend=trend)
    first_idx = max(0, len(closes) - days)
    return [(dates[h.index], h) for h in hits if h.index >= first_idx]
