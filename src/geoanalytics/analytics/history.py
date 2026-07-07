"""Загрузка истории дневных свечей с MOEX ISS в таблицу prices.

Нужна, чтобы у индикаторов (SMA/RSI/доходности) была история. Запускается
отдельной командой `geo backfill` (тяжёлая операция), а не в каждом цикле ингеста.

ISS отдаёт свечи постранично (до 500 за запрос) — поддержана пагинация через `start`.
Документация: https://iss.moex.com/iss/reference/
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import get_settings
from geoanalytics.core.dates import parse_moex_systime
from geoanalytics.core.logging import get_logger
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import Asset, Price

log = get_logger("analytics.history")

CANDLES_URL = (
    "https://iss.moex.com/iss/engines/stock/markets/shares/securities/{secid}/candles.json"
)
# Индексы (IMOEX и пр.) лежат в отдельном рынке ISS; формат свечей тот же (begin/OHLCV,
# volume у индекса может быть пустым — не критично, берём close).
INDEX_CANDLES_URL = (
    "https://iss.moex.com/iss/engines/stock/markets/index/securities/{secid}/candles.json"
)
# C2: фьючерсы FORTS лежат в срочном рынке ISS; формат свечей тот же (begin/OHLCV).
# secid — код торгуемого контракта (BRN6, SiM6…); базовый asset_code (BR/Si) свечей НЕ отдаёт,
# поэтому бэкфилл сперва находит ФРОНТАЛЬНЫЙ контракт по asset_code (`_front_futures_secid`).
FUTURES_CANDLES_URL = (
    "https://iss.moex.com/iss/engines/futures/markets/forts/securities/{secid}/candles.json"
)
# Список всех торгуемых контрактов FORTS (для резолва фронтального контракта по asset_code).
FORTS_SECURITIES_URL = (
    "https://iss.moex.com/iss/engines/futures/markets/forts/securities.json"
)
PAGE = 500  # размер страницы ISS
# Грузим историю окнами по WINDOW_DAYS дней. ISS на iss.moex.com может отдавать тело
# медленно/обрезанно при крупном ответе (на ограниченных каналах чтение всего диапазона
# отваливается по таймауту). Окно в ~90 дней даёт ~6 КБ на запрос — быстро и надёжно.
WINDOW_DAYS = 90
# Минимальный набор колонок свечи (begin/OHLCV) — меньше тело, больше свечей на запрос.
_CANDLE_COLS = "begin,open,high,low,close,volume"


@dataclass
class BackfillResult:
    ticker: str
    candles: int = 0
    error: str | None = None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def _get(url: str, params: dict) -> dict:
    resp = httpx.get(url, params=params, timeout=60.0)
    resp.raise_for_status()
    return resp.json()


def _rows(block: dict) -> list[dict]:
    cols = block["columns"]
    return [dict(zip(cols, row, strict=False)) for row in block["data"]]


def _fetch_window(url: str, frm: str, till: str, interval: int = 24) -> list[dict]:
    """Свечи за одно date-окно [frm, till] с пагинацией по start внутри окна.

    `interval` — код ISS (24=день по умолчанию; 1=мин, 10=10мин, 60=час — интрадей-путь T2.1).
    """
    out, start = [], 0
    while True:
        data = _get(url, {
            "from": frm, "till": till, "interval": interval, "start": start,
            "iss.meta": "off", "iss.only": "candles", "candles.columns": _CANDLE_COLS,
        })
        page = _rows(data["candles"])
        if not page:
            break
        out.extend(page)
        if len(page) < PAGE:
            break
        start += len(page)
    return out


def _fetch_candles(secid: str, days: int, kind: str = "share", interval: int = 24) -> list[dict]:
    """Свечи за `days`, загружаемые окнами по WINDOW_DAYS дней.

    `kind="index"` берёт свечи с индексного рынка ISS (для бенчмарка IMOEX); `kind="future"` —
    со срочного рынка FORTS (C2). `interval` — код ISS (24=день; 1/10/60 — интрадей T2.1); время
    бара (`begin`) парсит вызывающий (дневной путь схлопывает в полночь, интрадей — нет).
    """
    template = {"index": INDEX_CANDLES_URL, "future": FUTURES_CANDLES_URL}.get(kind, CANDLES_URL)
    url = template.format(secid=secid)
    today = datetime.now(UTC).date()
    start_date = today - timedelta(days=days)
    out: list[dict] = []
    win_start = start_date
    while win_start <= today:
        win_end = min(win_start + timedelta(days=WINDOW_DAYS - 1), today)
        out.extend(_fetch_window(url, win_start.strftime("%Y-%m-%d"),
                                 win_end.strftime("%Y-%m-%d"), interval=interval))
        win_start = win_end + timedelta(days=1)
    return out


# Кэш списка контрактов FORTS в пределах процесса: один бэкфилл-проход резолвит несколько
# фьючерсов, а список общий и большой (~600 строк) — не тянем его повторно на каждый тикер.
_FORTS_CACHE: dict[str, object] = {"ts": 0.0, "rows": None}
_FORTS_TTL = 1800.0


def _forts_securities() -> list[dict]:
    """Список контрактов FORTS (SECID/ASSETCODE/LASTTRADEDATE), кэш на процесс (TTL 30 мин)."""
    import time as _t

    now = _t.monotonic()
    if _FORTS_CACHE["rows"] is not None and now - float(_FORTS_CACHE["ts"]) < _FORTS_TTL:
        return _FORTS_CACHE["rows"]  # type: ignore[return-value]
    data = _get(FORTS_SECURITIES_URL, {
        "iss.meta": "off", "iss.only": "securities",
        "securities.columns": "SECID,ASSETCODE,LASTTRADEDATE",
    })
    rows = _rows(data["securities"])
    _FORTS_CACHE.update(ts=now, rows=rows)
    return rows


def _front_futures_secid(asset_code: str) -> str | None:
    """Фронтальный (ближайший по экспирации, ещё торгуемый) контракт для базового asset_code.

    Берёт контракты с совпадающим ASSETCODE и LASTTRADEDATE ≥ сегодня, возвращает с минимальной
    датой экспирации. None — нет активных контрактов (бэкфилл деградирует на 0 свечей).
    """
    today = datetime.now(UTC).date().isoformat()
    live = [r for r in _forts_securities()
            if r.get("ASSETCODE") == asset_code and (r.get("LASTTRADEDATE") or "") >= today]
    if not live:
        return None
    live.sort(key=lambda r: r["LASTTRADEDATE"])
    return live[0]["SECID"]


def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def backfill_asset(ticker: str, days: int | None = None) -> BackfillResult:
    """Грузит историю свечей одного актива."""
    days = days or get_settings().history_days
    result = BackfillResult(ticker=ticker.upper())
    with session_scope() as session:
        asset = session.scalars(select(Asset).where(Asset.ticker == ticker.upper())).first()
        if asset is None:
            result.error = "актив не найден (сначала geo db seed или geo ingest -s moex)"
            return result
        # Фьючерсы: резолвим фронтальный контракт по asset_code (секид чувствителен к регистру —
        # SiM6 со строчной i, поэтому НЕ uppercase'им). Базовый код свечей не отдаёт.
        secid = ticker.upper()
        if asset.kind == "future":
            from geoanalytics.storage.seed import FUTURES

            asset_code = (FUTURES.get(ticker.upper(), (None, None))[1]) or ticker.upper()
            front = _front_futures_secid(asset_code)
            if front is None:
                result.error = f"нет активного контракта FORTS для {asset_code}"
                log.warning("futures_no_front_contract", ticker=ticker, asset_code=asset_code)
                return result
            secid = front
        try:
            candles = _fetch_candles(secid, days, kind=asset.kind)
        except Exception as exc:  # noqa: BLE001
            result.error = str(exc)
            log.error("backfill_failed", ticker=ticker, error=str(exc))
            return result

        for c in candles:
            ts = parse_moex_systime(c.get("begin"))
            close = _to_float(c.get("close"))
            if ts is None or close is None:
                continue
            stmt = (
                pg_insert(Price)
                .values(
                    asset_id=asset.id, ts=ts, interval="1d",
                    open=_to_float(c.get("open")) or close,
                    high=_to_float(c.get("high")) or close,
                    low=_to_float(c.get("low")) or close,
                    close=close,
                    volume=_to_float(c.get("volume")),
                )
                .on_conflict_do_nothing(constraint="uq_price_point")
            )
            if session.execute(stmt).rowcount:
                result.candles += 1
    log.info("backfill_done", ticker=result.ticker, candles=result.candles)
    return result


def backfill_all(days: int | None = None) -> list[BackfillResult]:
    """Грузит историю по всем известным активам из справочника."""
    with session_scope() as session:
        tickers = [t for (t,) in session.execute(select(Asset.ticker))]
    return [backfill_asset(t, days) for t in tickers]


# --------------------------------------------------------------------------- #
# История курсов валют (ЦБ XML_dynamic) — факторы для G2/G3.
# Живой коннектор CBR даёт только текущий день; для vol-режимов и факторной
# атрибуции нужна многолетняя история USDRUB.
# --------------------------------------------------------------------------- #

FX_DYNAMIC_URL = "https://www.cbr.ru/scripts/XML_dynamic.asp"
# Внутренние коды валют ЦБ для XML_dynamic (VAL_NM_RQ).
CBR_CURRENCY_IDS = {"USD": "R01235", "EUR": "R01239", "CNY": "R01375"}


def parse_fx_dynamic(xml_bytes: bytes) -> list[tuple[datetime, float]]:
    """Пары (ts UTC-полночь, курс за единицу) из ответа XML_dynamic ЦБ.

    Берём VunitRate (курс за 1 единицу — у CNY номинал бывает 10/100);
    десятичный разделитель — запятая. Битые записи пропускаются. Чистая функция.
    """
    import xml.etree.ElementTree as ET

    out: list[tuple[datetime, float]] = []
    for rec in ET.fromstring(xml_bytes).iter("Record"):
        raw_date = rec.get("Date")
        node = rec.find("VunitRate")
        if raw_date is None or node is None or not node.text:
            continue
        try:
            d = datetime.strptime(raw_date, "%d.%m.%Y").replace(tzinfo=UTC)
            value = float(node.text.replace(",", "."))
        except ValueError:
            continue
        out.append((d, value))
    return out


@dataclass
class FxBackfillResult:
    currency: str
    points: int = 0
    error: str | None = None


def backfill_fx(currencies: list[str] | None = None,
                days: int | None = None) -> list[FxBackfillResult]:
    """Грузит историю официальных курсов ЦБ в fx_rates (idempotent do-nothing).

    Один запрос на валюту — XML_dynamic отдаёт весь диапазон сразу.
    """
    from geoanalytics.storage.models import FxRate

    days = days or get_settings().history_days
    currencies = [c.upper() for c in (currencies or list(CBR_CURRENCY_IDS))]
    today = datetime.now(UTC).date()
    frm = (today - timedelta(days=days)).strftime("%d/%m/%Y")
    till = today.strftime("%d/%m/%Y")

    results: list[FxBackfillResult] = []
    for cur in currencies:
        res = FxBackfillResult(currency=cur)
        code = CBR_CURRENCY_IDS.get(cur)
        if code is None:
            res.error = f"нет кода ЦБ (известны: {', '.join(CBR_CURRENCY_IDS)})"
            results.append(res)
            continue
        try:
            resp = httpx.get(FX_DYNAMIC_URL, timeout=60.0, params={
                "date_req1": frm, "date_req2": till, "VAL_NM_RQ": code,
            })
            resp.raise_for_status()
            points = parse_fx_dynamic(resp.content)
        except Exception as exc:  # noqa: BLE001 — одна валюта не валит бэкфилл
            res.error = str(exc)
            log.error("backfill_fx_failed", currency=cur, error=str(exc))
            results.append(res)
            continue
        with session_scope() as session:
            for ts, value in points:
                stmt = (
                    pg_insert(FxRate)
                    .values(currency=cur, ts=ts, value=value)
                    .on_conflict_do_nothing(constraint="uq_fx_point")
                )
                if session.execute(stmt).rowcount:
                    res.points += 1
        log.info("backfill_fx_done", currency=cur, points=res.points)
        results.append(res)
    return results


# --------------------------------------------------------------------------- #
# История учётных цен драгметаллов ЦБ (xml_metall) — сырьевые факторы.
# Один запрос отдаёт весь диапазон по всем четырём металлам сразу. Цены в
# рублях за грамм — рублёвая цена и есть выручка наших добытчиков (PLZL/GMKN),
# в отличие от долларовых фьючерсов FORTS (без склейки контрактов истории нет).
# --------------------------------------------------------------------------- #

METALL_DYNAMIC_URL = "https://www.cbr.ru/scripts/xml_metall.asp"
# Коды металлов ЦБ → имя индикатора в macro_series.
CBR_METAL_CODES = {"1": "gold", "2": "silver", "3": "platinum", "4": "palladium"}


def parse_metal_dynamic(xml_bytes: bytes) -> list[tuple[str, datetime, float]]:
    """Тройки (индикатор, ts UTC-полночь, цена ₽/г) из ответа xml_metall ЦБ.

    Берём <Sell> (учётная цена); десятичный разделитель — запятая.
    Неизвестные коды и битые записи пропускаются. Чистая функция.
    """
    import xml.etree.ElementTree as ET

    out: list[tuple[str, datetime, float]] = []
    for rec in ET.fromstring(xml_bytes).iter("Record"):
        indicator = CBR_METAL_CODES.get(rec.get("Code") or "")
        raw_date = rec.get("Date")
        node = rec.find("Sell")
        if indicator is None or raw_date is None or node is None or not node.text:
            continue
        try:
            d = datetime.strptime(raw_date, "%d.%m.%Y").replace(tzinfo=UTC)
            value = float(node.text.replace(",", "."))
        except ValueError:
            continue
        out.append((indicator, d, value))
    return out


@dataclass
class MetalBackfillResult:
    points: int = 0
    error: str | None = None


def backfill_metals(days: int | None = None) -> MetalBackfillResult:
    """Грузит историю учётных цен металлов ЦБ в macro_series (idempotent)."""
    from geoanalytics.storage.models import MacroSeries

    days = days or get_settings().history_days
    today = datetime.now(UTC).date()
    res = MetalBackfillResult()
    try:
        resp = httpx.get(METALL_DYNAMIC_URL, timeout=60.0, params={
            "date_req1": (today - timedelta(days=days)).strftime("%d/%m/%Y"),
            "date_req2": today.strftime("%d/%m/%Y"),
        })
        resp.raise_for_status()
        points = parse_metal_dynamic(resp.content)
    except Exception as exc:  # noqa: BLE001 — сетевая ошибка не должна ронять CLI
        res.error = str(exc)
        log.error("backfill_metals_failed", error=str(exc))
        return res
    with session_scope() as session:
        for indicator, ts, value in points:
            stmt = (
                pg_insert(MacroSeries)
                .values(indicator=indicator, ts=ts, value=value, unit="RUB/g")
                .on_conflict_do_nothing(constraint="uq_macro_point")
            )
            if session.execute(stmt).rowcount:
                res.points += 1
    log.info("backfill_metals_done", points=res.points)
    return res


# --------------------------------------------------------------------------- #
# История нефти Brent из FRED (DCOILBRENTEU) — сырьевой фактор атрибуции/whatif.
# Живой коннектор brent (FORTS, фронт-месяц) пишет ту же серию macro_series
# 'brent' в тех же единицах (USD/bbl, как контракт BR), поэтому история FRED и
# свежий хвост FORTS стыкуются без скачка масштаба — фактор считается на
# доходностях по непрерывному ряду. Без ключа FRED источник тихо пропускается.
# --------------------------------------------------------------------------- #

FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_BRENT_SERIES = "DCOILBRENTEU"  # Crude Oil Brent, USD/баррель, дневная


def parse_fred_observations(payload: dict) -> list[tuple[datetime, float]]:
    """Пары (ts UTC-полночь, значение) из ответа FRED observations.

    Пустые наблюдения FRED помечает значением '.', их пропускаем. Чистая функция.
    """
    out: list[tuple[datetime, float]] = []
    for obs in payload.get("observations", []):
        raw = obs.get("value")
        raw_date = obs.get("date")
        if raw in (None, "", ".") or not raw_date:
            continue
        try:
            ts = datetime.strptime(raw_date, "%Y-%m-%d").replace(tzinfo=UTC)
            value = float(raw)
        except ValueError:
            continue
        out.append((ts, value))
    return out


@dataclass
class FredBrentBackfillResult:
    points: int = 0
    error: str | None = None


def backfill_fred_brent(days: int | None = None) -> FredBrentBackfillResult:
    """Грузит историю Brent из FRED в macro_series indicator='brent' (idempotent).

    Активирует brent-фактор атрибуции/whatif (нужно ≥60 точек). Без GEO_FRED_API_KEY
    возвращает результат с error, не падая.
    """
    from geoanalytics.storage.models import MacroSeries

    res = FredBrentBackfillResult()
    api_key = get_settings().fred_api_key
    if not api_key:
        res.error = "нет GEO_FRED_API_KEY — бэкфилл Brent пропущен"
        log.warning("backfill_fred_brent_no_key")
        return res

    days = days or get_settings().history_days
    start = (datetime.now(UTC).date() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        payload = _get(FRED_OBSERVATIONS_URL, {
            "series_id": FRED_BRENT_SERIES,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": start,
        })
        points = parse_fred_observations(payload)
    except Exception as exc:  # noqa: BLE001 — сетевая ошибка не должна ронять CLI
        res.error = str(exc)
        log.error("backfill_fred_brent_failed", error=str(exc))
        return res
    with session_scope() as session:
        for ts, value in points:
            stmt = (
                pg_insert(MacroSeries)
                .values(indicator="brent", ts=ts, value=value, unit="USD/bbl")
                .on_conflict_do_nothing(constraint="uq_macro_point")
            )
            if session.execute(stmt).rowcount:
                res.points += 1
    log.info("backfill_fred_brent_done", points=res.points)
    return res
