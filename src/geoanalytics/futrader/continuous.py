"""Трек 2 / T2.1: склейка непрерывного фьючерсного контракта из посерийных свечей по контрактам.

Проблема: фьючерс торгуется сериями контрактов с экспирацией; на роллах уровень цены скачет (спред
между контрактами). Для моделирования нужен непрерывный ряд. Метод v1 (ratio/Panama): держим
последний контракт «как есть», а более ранние ретро-корректируем умножением на коэффициент в точке
стыка, чтобы цена была непрерывной. Чистое ядро `stitch_continuous` тестируется без БД.

Ограничения v1: ролл по дате экспирации (не по OI/объёму); коэффициент — отношение closes на стыке
сегментов (видимый шов убирается). Уточнения (OI-ролл, разностный метод) — позже.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class ContBar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    contract_secid: str


@dataclass
class ContinuousSeries:
    bars: list[ContBar] = field(default_factory=list)
    rolls: list[dict] = field(default_factory=list)   # [{ts, from_secid, to_secid, factor}]


def _segments(contracts: list[dict]) -> list[tuple[str, list[dict]]]:
    """Назначить каждому контракту его «фронт-окно» (prev_expiry, expiry] из собственных баров.

    Контракты — по возрастанию экспирации. Каждый отдаёт бары периода, когда он фронтальный:
    после экспирации предыдущего и до своей. Последний контракт берёт всё после предыдущего."""
    items = [c for c in contracts if c.get("bars")]
    items.sort(key=lambda c: (c.get("expiry") is None, c.get("expiry") or date.max))
    segments: list[tuple[str, list[dict]]] = []
    prev_exp: date | None = None
    for k, c in enumerate(items):
        this_exp: date | None = c.get("expiry")
        is_last = k == len(items) - 1
        seg = []
        for b in c["bars"]:
            d = b["ts"].date()
            if prev_exp is not None and d <= prev_exp:
                continue
            if not is_last and this_exp is not None and d > this_exp:
                continue
            seg.append(b)
        if seg:
            seg.sort(key=lambda b: b["ts"])
            segments.append((c["secid"], seg))
        if this_exp is not None:
            prev_exp = this_exp
    return segments


def stitch_continuous(contracts: list[dict], method: str = "ratio") -> ContinuousSeries:
    """Склеить непрерывный контракт из контрактов (каждый: {secid, expiry(date|None), bars[...]}).

    `bars` — [{ts(datetime, tz), open, high, low, close, volume}]. Возвращает непрерывные бары
    (хронологически) и список роллов с коэффициентами. Последний контракт — без корректировки;
    более ранние умножены на накопленный коэффициент стыков (ratio)."""
    segments = _segments(contracts)
    if not segments:
        return ContinuousSeries()
    m = len(segments)
    adj = [1.0] * m
    rolls: list[dict] = []
    for k in range(m - 2, -1, -1):
        last_close = segments[k][1][-1]["close"]
        first_next = segments[k + 1][1][0]["close"]
        factor = (first_next / last_close) if last_close else 1.0
        adj[k] = adj[k + 1] * factor
        rolls.append({"ts": segments[k + 1][1][0]["ts"], "from_secid": segments[k][0],
                      "to_secid": segments[k + 1][0], "factor": round(factor, 6)})
    rolls.reverse()

    bars: list[ContBar] = []
    for k, (secid, seg) in enumerate(segments):
        f = adj[k]
        for b in seg:
            bars.append(ContBar(
                ts=b["ts"], open=b["open"] * f, high=b["high"] * f, low=b["low"] * f,
                close=b["close"] * f, volume=b.get("volume"), contract_secid=secid))
    bars.sort(key=lambda x: x.ts)
    return ContinuousSeries(bars=bars, rolls=rolls)


def continuous_series(session, ticker: str, interval: str = "1h",
                      method: str = "ratio") -> ContinuousSeries:
    """DB-раннер: собрать контракты из `futures_candles` и склеить непрерывный ряд."""
    from geoanalytics.futrader.data import _asset_code_for
    from geoanalytics.storage.repositories import FuturesCandleRepository

    repo = FuturesCandleRepository(session)
    asset_code = _asset_code_for(ticker)
    contracts: list[dict] = []
    for secid, expiry in repo.contracts(asset_code, interval):
        bars = [{"ts": r.ts, "open": r.open, "high": r.high, "low": r.low,
                 "close": r.close, "volume": r.volume}
                for r in repo.contract_series(secid, interval)]
        contracts.append({"secid": secid, "expiry": expiry, "bars": bars})
    return stitch_continuous(contracts, method=method)
