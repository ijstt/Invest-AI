"""Трек 2 / Фаза A→C: захват микроструктуры стакана фьючерсов FORTS с ISS.

ЧЕСТНАЯ ОГОВОРКА ОБ ИСТОЧНИКЕ: полный L2-ладдер (orderbook.json) MOEX анонимно НЕ отдаёт (требует
платный/авторизованный фид — отвечает HTML). Зато блок `marketdata` фронт-контракта отдаёт
АГРЕГАТНУЮ глубину анонимно: BID/OFFER (лучшие), SPREAD, BIDDEPTHT/OFFERDEPTHT (суммарный объём
бид/аск), NUMBIDS/NUMOFFERS. Этого достаточно для ключевого сигнала микроструктуры — ДИСБАЛАНСА
стакана (bid_vol−ask_vol)/(…) и спреда. Полный ладдер (bids/asks JSON) остаётся null.

ISS отдаёт лишь МГНОВЕННЫЙ снимок (истории нет) — копим ТОЛЬКО ВПЕРЁД: служба `geo-depth`
(CLI `geo futures-depth capture`) каждые N секунд пишет снимок в `futures_orderbook` (0037).
Фичи дисбаланса/спреда и depth-aware филлы подключаются в Фазе C, когда накопится история.

Время снимка храним MSK-настенно с UTC-меткой (как свечи MOEX, см. `futrader.session`) — чтобы
снимки чисто джойнились к барам в Фазе C. Чистое ядро `parse_depth` тестируется без сети.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta, timezone

import httpx

from geoanalytics.core.logging import get_logger
from geoanalytics.futrader.accumulate import DEFAULT_TICKERS
from geoanalytics.futrader.session import in_session

log = get_logger("futrader.depth")

# Блок marketdata фронт-контракта (анонимно) — best bid/ask + АГРЕГАТНАЯ глубина. Полный L2 платный.
MARKETDATA_URL = (
    "https://iss.moex.com/iss/engines/futures/markets/forts/securities/{secid}.json"
)
_MSK = timezone(timedelta(hours=3))
DEFAULT_TOP = 10               # параметр совместимости (полный ладдер недоступен) — не используется
SECID_REFRESH_SEC = 3600.0     # как часто пере-резолвить фронт-контракт (роллы редки)


def _num(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def parse_depth(payload: dict) -> dict | None:
    """Чистый разбор блока `marketdata` ISS → скаляры микроструктуры (best/спред/объёмы/дисбаланс).

    Полный L2-ладдер недоступен анонимно → bids/asks=None. None — пустой блок (вне сессии/битый)."""
    block = payload.get("marketdata", {})
    cols = block.get("columns", [])
    data = block.get("data", [])
    if not cols or not data:
        return None
    idx = {c: i for i, c in enumerate(cols)}
    row = data[0]

    def g(name):
        i = idx.get(name)
        return row[i] if i is not None else None

    best_bid, best_ask = _num(g("BID")), _num(g("OFFER"))
    spread = _num(g("SPREAD"))
    bid_vol, ask_vol = _num(g("BIDDEPTHT")), _num(g("OFFERDEPTHT"))
    num_bids = g("NUMBIDS")
    # MOEX анонимно для FORTS часто отдаёт только SPREAD (BID/OFFER/глубина — null). Снимок пишем,
    # если есть ХОТЬ ЧТО-ТО (спред — минимум); полностью пустой блок (рынок закрыт) → None.
    if (best_bid is None and best_ask is None and bid_vol is None
            and ask_vol is None and spread is None):
        return None
    imbalance = None
    if bid_vol is not None and ask_vol is not None and (bid_vol + ask_vol) > 0:
        imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)
    if spread is None and best_bid is not None and best_ask is not None:
        spread = best_ask - best_bid
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "bid_vol": bid_vol,
        "ask_vol": ask_vol,
        "imbalance": imbalance,
        "levels": int(num_bids) if num_bids is not None else None,   # число бид-заявок (агрегат)
        "bids": None,                                # полный L2-ладдер анонимно недоступен
        "asks": None,
    }


def fetch_depth(secid: str, *, timeout: float = 15.0) -> dict | None:
    """HTTP-снимок микроструктуры контракта (блок marketdata) → разбор. Бросает при сбое сети."""
    resp = httpx.get(MARKETDATA_URL.format(secid=secid), timeout=timeout,
                     params={"iss.meta": "off", "iss.only": "marketdata"})
    resp.raise_for_status()
    return parse_depth(resp.json())


def capture_ts() -> datetime:
    """Время снимка: MSK-настенное с UTC-меткой (как свечи) — для джойна к барам в Фазе C."""
    return datetime.now(_MSK).replace(tzinfo=UTC, microsecond=0)


def _resolve_secids(tickers) -> dict[str, str]:
    """{ticker: фронт-контракт secid}. Пропускает нерезолвящиеся (тонкие/неизвестные)."""
    from geoanalytics.analytics.history import _front_futures_secid
    from geoanalytics.futrader.data import _asset_code_for

    out: dict[str, str] = {}
    for tk in tickers:
        try:
            secid = _front_futures_secid(_asset_code_for(tk))
        except Exception as exc:  # noqa: BLE001 — один инструмент не валит резолв
            log.warning("depth_secid_failed", ticker=tk, error=str(exc))
            continue
        if secid:
            out[tk] = secid
    return out


def capture_once(session, *, tickers=DEFAULT_TICKERS, ts: datetime | None = None,
                 secids: dict[str, str] | None = None) -> int:
    """Один проход: снять микроструктуру фронт-контрактов, записать снимки. Вернёт число записей."""
    from geoanalytics.futrader.data import _asset_code_for
    from geoanalytics.storage.repositories import FuturesOrderbookRepository

    repo = FuturesOrderbookRepository(session)
    ts = ts or capture_ts()
    secids = secids if secids is not None else _resolve_secids(tickers)
    stored = 0
    for tk in tickers:
        secid = secids.get(tk)
        if not secid:
            continue
        try:
            snap = fetch_depth(secid)
        except Exception as exc:  # noqa: BLE001 — сетевой сбой одного инструмента не валит проход
            log.warning("depth_fetch_failed", ticker=tk, secid=secid, error=str(exc))
            continue
        if snap is None:
            continue
        repo.add(asset_code=_asset_code_for(tk), contract_secid=secid, ts=ts, **snap)
        stored += 1
    return stored


def capture_loop(*, interval_sec: float = 5.0, tickers=DEFAULT_TICKERS,
                 only_in_session: bool = True) -> None:
    """Служба geo-depth: бесконечный цикл снятия микроструктуры каждые `interval_sec` секунд.

    Снимаем только когда идёт сессия (`only_in_session`, основная+вечерняя) — вне сессии стакан
    пуст. Фронт-контракт кэшируем и пере-резолвим раз в час (роллы редки)."""
    from geoanalytics.storage.db import session_scope

    log.info("depth_capture_start", interval_sec=interval_sec, tickers=list(tickers))
    secids = _resolve_secids(tickers)
    last_resolve = time.monotonic()
    while True:
        try:
            now = capture_ts()
            if only_in_session and not in_session(now, evening=True):
                time.sleep(interval_sec)
                continue
            if time.monotonic() - last_resolve > SECID_REFRESH_SEC:
                secids = _resolve_secids(tickers)
                last_resolve = time.monotonic()
            with session_scope() as session:
                n = capture_once(session, tickers=tickers, ts=now, secids=secids)
            if n:
                log.info("depth_capture", stored=n, ts=now.isoformat())
        except Exception as exc:  # noqa: BLE001 — цикл не должен падать (служба безоператорна)
            log.error("depth_capture_error", error=str(exc))
        time.sleep(interval_sec)
