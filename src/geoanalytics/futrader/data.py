"""Трек 2 / T2.1: загрузка интрадей-свечей фьючерсов FORTS ПО КОНТРАКТАМ.

Изолированный слой данных форка. Переиспользует ISS-фетч и список контрактов из
`analytics.history`; пишет в отдельную `futures_candles` (НЕ в `prices`), с идентичностью
контракта (secid + expiry). Время бара НЕ схлопывается в полночь (`to_day=False`) — это интрадей.
ISS отдаёт минутную историю лишь за недавнее окно — берём что есть (graceful).
"""

from __future__ import annotations

from datetime import date

from geoanalytics.analytics.history import (
    FORTS_SECURITIES_URL,
    _fetch_candles,
    _forts_securities,
    _get,
    _rows,
    _to_float,
)
from geoanalytics.core.dates import parse_moex_systime
from geoanalytics.core.logging import get_logger

log = get_logger("futrader.data")

# Метка интервала → код ISS (1=минута, 10=10мин, 60=час, 24=день).
# День добавлен для ГЛУБИНЫ (Фаза 0): часовой/дневной ряд по контракту длиннее минутного окна ISS,
# а пишется ПО КОНТРАКТУ со склейкой (чего нет в `prices`, где фьючерс — один фронт-ролл без меток).
INTERVAL_CODES = {"1m": 1, "10m": 10, "1h": 60, "1d": 24}


def _asset_code_for(ticker: str) -> str:
    """Тикер фьючерса (BR/GD/SI…) → ISS asset_code (BR/GOLD/Si, case-sensitive)."""
    from geoanalytics.storage.seed import FUTURES

    return (FUTURES.get(ticker.upper(), (None, None))[1]) or ticker.upper()


def list_contracts(asset_code: str) -> list[dict]:
    """Котируемые контракты asset_code из ISS: [{secid, expiry}] по возрастанию экспирации."""
    out = [{"secid": r["SECID"], "expiry": r.get("LASTTRADEDATE")}
           for r in _forts_securities() if r.get("ASSETCODE") == asset_code]
    out.sort(key=lambda r: r["expiry"] or "")
    return out


def _parse_expiry(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def fetch_contract_spec(secid: str):
    """Спецификация контракта `secid` с ISS FORTS → `ContractSpec` (None, если контракт не найден).

    Тянет MINSTEP/STEPPRICE/INITIALMARGIN/BUYSELLFEE — для маржинального симулятора T2.2.
    """
    from geoanalytics.futrader.execution import ContractSpec

    data = _get(FORTS_SECURITIES_URL, {
        "iss.meta": "off", "iss.only": "securities",
        "securities.columns": "SECID,MINSTEP,STEPPRICE,INITIALMARGIN,BUYSELLFEE",
    })
    for r in _rows(data["securities"]):
        if r.get("SECID") == secid:
            step = _to_float(r.get("MINSTEP"))
            value = _to_float(r.get("STEPPRICE"))
            if not step or value is None:
                return None
            return ContractSpec(
                secid=secid, tick_size=step, tick_value=value,
                initial_margin=_to_float(r.get("INITIALMARGIN")) or 0.0,
                fee=_to_float(r.get("BUYSELLFEE")) or 0.0,
            )
    return None


def backfill_futures_intraday(session, ticker: str, *, interval: str = "1m",
                              days: int = 7, max_contracts: int = 3) -> int:
    """Тянет интрадей-свечи фьючерса по ближайшим контрактам → `futures_candles`. Число свечей.

    `ticker` — наш тикер (резолвится в ISS asset_code); `interval` ∈ 1m/10m/1h; берём до
    `max_contracts` ближайших по экспирации контрактов. Идемпотентно; сбой ISS по одному контракту
    не роняет остальные."""
    from geoanalytics.storage.repositories import FuturesCandleRepository

    if interval not in INTERVAL_CODES:
        raise ValueError(f"interval должен быть из {list(INTERVAL_CODES)}")
    asset_code = _asset_code_for(ticker)
    code = INTERVAL_CODES[interval]
    contracts = list_contracts(asset_code)[:max_contracts]
    rows: list[dict] = []
    for c in contracts:
        try:
            raw = _fetch_candles(c["secid"], days, kind="future", interval=code)
        except Exception as exc:  # noqa: BLE001 — сеть/ISS не должны ронять весь бэкфилл
            log.warning("futures_intraday_fetch_failed", secid=c["secid"], error=str(exc))
            continue
        expiry = _parse_expiry(c["expiry"])
        for cd in raw:
            ts = parse_moex_systime(cd.get("begin"), to_day=False)
            close = _to_float(cd.get("close"))
            if ts is None or close is None:
                continue
            rows.append({
                "asset_code": asset_code, "contract_secid": c["secid"], "expiry": expiry,
                "ts": ts, "interval": interval,
                "open": _to_float(cd.get("open")) or close,
                "high": _to_float(cd.get("high")) or close,
                "low": _to_float(cd.get("low")) or close,
                "close": close, "volume": _to_float(cd.get("volume")),
            })
    added = FuturesCandleRepository(session).upsert_many(rows)
    log.info("futures_intraday_done", ticker=ticker.upper(), asset_code=asset_code,
             interval=interval, contracts=len(contracts), fetched=len(rows), added=added)
    return added
