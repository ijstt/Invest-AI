"""Торговый календарь FORTS для сессионной дисциплины интрадей-форка (Трек 2, Фаза A).

Интрадей-сделка НЕ должна переживать закрытие сессии и висеть овернайт. Здесь — чистые
функции времени (без БД): идёт ли сессия, пора ли принудительно флэтить, разрешён ли вход,
пересекла ли позиция границу торгового дня. Время свечей/решений в проекте — UTC; внутри
переводим в MSK (FORTS живёт в MSK).

По умолчанию торгуем ОСНОВНУЮ сессию (09:00–18:45 MSK) и флэтим за `flat_before_min` до
закрытия (не под неликвидный аукцион). Вечерняя сессия (до 23:50 MSK) — опциональна
(`evening=True` расширяет торговый день). Праздничный календарь FORTS жёстко не моделируем:
рабочие субботы/выходные сессии (перенос праздников) включаются флагом `allow_weekend`, а
реальную «открыта ли биржа» в эти дни ловит наличие свежего бара (stale-гейт в paper) — на
закрытом выходном/празднике свежих баров нет, вход блокируется устареванием. ОСТОРОЖНОСТЬ:
ликвидность выходных/вечерних сессий кратно ниже (`low_liquidity_session` → срез размера).

ВАЖНО о времени: свечи MOEX в проекте хранятся MSK-НАСТЕННО, но с UTC-меткой
(`parse_moex_systime` делает `.replace(tzinfo=UTC)` над SYSTIME МосБиржи). Поэтому настенные
часы ts уже MSK — конверсию НЕ делаем, лишь отбрасываем tz-метку. Это подтверждается
распределением часов 1h-свечей (9–23 = основная+вечерняя FORTS, не 00–02 как было бы в UTC).

Точные часы могут уточняться в Фазе B по реальным данным; критичные для корректности границы —
ЗАКРЫТИЕ (18:45 осн. / 23:50 веч.) и клиринговые перерывы — заданы консервативно.
"""

from __future__ import annotations

from datetime import date, datetime, time

MAIN_OPEN = time(9, 0)          # утреннее открытие FORTS
MAIN_CLOSE = time(18, 45)       # закрытие основной (дневной+вечерней дневной) сессии
EVENING_CLOSE = time(23, 50)    # закрытие вечерней сессии
# Клиринговые перерывы (новых входов нет; позиции держатся сквозь — это НЕ закрытие дня).
INTRADAY_CLEARING = (time(14, 0), time(14, 5))
EVENING_BREAK = (MAIN_CLOSE, time(19, 0))          # 18:45–19:00 перерыв перед вечерней


def _msk(ts: datetime) -> datetime:
    """Настенное MSK-время бара. Свечи MOEX хранятся MSK-настенно с UTC-меткой (см. модульный
    докстринг) — конверсию не делаем, лишь отбрасываем tz: настенные часы трактуем как MSK."""
    return ts.replace(tzinfo=None)


def is_trading_day(d: date, *, allow_weekend: bool = False) -> bool:
    """Торговый ли день. Будни — да. Рабочие субботы/выходные сессии MOEX (перенос праздников)
    редки и с ТОНКОЙ ликвидностью: при `allow_weekend=True` выходной тоже считаем торговым, а
    реальную «открыта ли биржа» определяет наличие свежего бара (stale-гейт в paper) — на
    закрытом выходном свежих баров нет, вход блокируется устареванием, флэт даёт crossed_session.
    Праздничные будни (биржа закрыта) так же отсекаются stale-гейтом."""
    return d.isoweekday() <= 5 or allow_weekend


def _close_time(evening: bool) -> time:
    return EVENING_CLOSE if evening else MAIN_CLOSE


def in_session(ts: datetime, *, evening: bool = False, allow_weekend: bool = False) -> bool:
    """Идёт ли торговая сессия в момент ts (MSK), вне клиринговых перерывов."""
    m = _msk(ts)
    if not is_trading_day(m.date(), allow_weekend=allow_weekend):
        return False
    t = m.time()
    if not (MAIN_OPEN <= t < _close_time(evening)):
        return False
    if INTRADAY_CLEARING[0] <= t < INTRADAY_CLEARING[1]:
        return False
    if evening and EVENING_BREAK[0] <= t < EVENING_BREAK[1]:
        return False
    return True


def minutes_to_close(ts: datetime, *, evening: bool = False) -> float:
    """Минут до закрытия торгового дня (MSK); ≤0 если уже после закрытия."""
    m = _msk(ts)
    close = datetime.combine(m.date(), _close_time(evening))   # наивное MSK
    return (close - m).total_seconds() / 60.0


def force_flat_due(ts: datetime, *, flat_before_min: float = 15, evening: bool = False,
                   allow_weekend: bool = False) -> bool:
    """Пора ли принудительно флэтить позицию к моменту ts.

    В торговый день: True если до закрытия осталось ≤ `flat_before_min` (в т.ч. уже после
    закрытия — напр. вечерний бар при evening=False). До утреннего открытия — True (позиция
    не должна жить ночью). Вне торгового дня (выходные, если не allow_weekend) — True (овернайт-
    страховка). При allow_weekend рабочая суббота — нормальный день, флэт лишь к её закрытию."""
    m = _msk(ts)
    if not is_trading_day(m.date(), allow_weekend=allow_weekend):
        return True
    if m.time() < MAIN_OPEN:
        return True
    return minutes_to_close(ts, evening=evening) <= flat_before_min


# Буферы клиринга (новых входов нет: расширенный спред/снятие стакана).
# 1. Дневной промежуточный клиринг: 13:55–14:10 MSK (клиринг 14:00–14:05).
# 2. Вечерний основной клиринг: 18:40–19:10 MSK (клиринг 18:45–19:00).
CLEARING_BUFFERS = (
    (time(13, 55), time(14, 10)),
    (time(18, 40), time(19, 10)),
)


def in_clearing_window(ts: datetime) -> bool:
    """Находится ли момент ts (MSK) в буферной зоне клиринга MOEX FORTS?"""
    t = _msk(ts).time()
    for start, end in CLEARING_BUFFERS:
        if start <= t < end:
            return True
    return False


def entry_allowed(ts: datetime, *, flat_before_min: float = 15, evening: bool = False,
                  allow_weekend: bool = False) -> bool:
    """Разрешён ли НОВЫЙ вход: сессия идёт, НЕ в окне закрытия И НЕ в буфере клиринга."""
    if in_clearing_window(ts):
        return False
    return in_session(ts, evening=evening, allow_weekend=allow_weekend) and not force_flat_due(
        ts, flat_before_min=flat_before_min, evening=evening, allow_weekend=allow_weekend)


def low_liquidity_session(ts: datetime, *, evening: bool = False) -> bool:
    """Тонкая сессия — ОСТОРОЖНОСТЬ: размер позиции стоит срезать. True для выходного дня
    (рабочая суббота — ликвидность кратно ниже будней) и для вечерней сессии (после 18:45 MSK).
    Будни в основные часы — нормальная ликвидность (False)."""
    m = _msk(ts)
    if m.date().isoweekday() > 5:
        return True
    if evening and m.time() >= MAIN_CLOSE:
        return True
    return False


def session_date(ts: datetime) -> date:
    """Дата торгового дня (MSK). День FORTS (с вечерней) не пересекает полночь → это MSK-дата."""
    return _msk(ts).date()


def crossed_session(entry_ts: datetime, ref_ts: datetime) -> bool:
    """Пересекла ли позиция границу торгового дня (овернайт): день ref позже дня входа."""
    return session_date(ref_ts) > session_date(entry_ts)
