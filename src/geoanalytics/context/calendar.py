"""H2 (Волна 3): календарь событий — заседания ЦБ по ставке и дивидендные отсечки.

Календарь знает даты ЗАРАНЕЕ — в отличие от extraction-событий из новостей.
Это переводит аналитику из реактивной в проактивную («завтра заседание ЦБ»,
«завтра отсечка SBER») и даёт точные опорные даты для event study (E1) и
будущего surprise-классификатора (F10).

Источники:
- ЦБ РФ: страница графика заседаний СД по ключевой ставке (HTML, активная
  вкладка текущего года) — https://www.cbr.ru/dkp/cal_mp/;
- MOEX ISS: дивиденды по бумаге (registryclosedate = дата отсечки) —
  /iss/securities/{SECID}/dividends.json. ВНИМАНИЕ: эндпоинт отдаёт только
  СОСТОЯВШИЕСЯ выплаты (проверено 2026-06-11: max дата — прошлый сезон);
- smart-lab.ru/dividends: ОБЪЯВЛЕННЫЕ (будущие) отсечки — замена H1-ленты
  сущфактов: e-disclosure.ru закрыт антиботом ServicePipe (см. память H1),
  СКРИН/АЗИПИ не покрывают наших эмитентов. dedup_key совпадает с MOEX-овским,
  так что когда отсечка состоится и появится в ISS, записи сольются.

Синк идемпотентен (upsert по dedup_key), история не удаляется. Ежедневный
запуск из scheduler `_daily_jobs`; руками — `geo calendar --sync`.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from geoanalytics.core.logging import get_logger
from geoanalytics.storage.models import Asset, CalendarEvent

log = get_logger("context.calendar")

CBR_CALENDAR_URL = "https://www.cbr.ru/dkp/cal_mp/"
ISS_DIVIDENDS_URL = "https://iss.moex.com/iss/securities/{ticker}/dividends.json"
SMARTLAB_DIVIDENDS_URL = "https://smart-lab.ru/dividends/"
# Без браузерного UA smart-lab может отдавать заглушку.
_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/127.0"}

KIND_CBR = "cbr_rate_meeting"
KIND_DIVIDEND = "dividend_cutoff"

# Окно релевантности при синке ЦБ: страница содержит и архивные вкладки —
# защищаемся от случайного захвата чужого года при смене вёрстки.
_CBR_WINDOW_DAYS = 400

_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11,
    "декабря": 12,
}
_DATE_RE = re.compile(
    r"(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})", re.IGNORECASE
)


@dataclass
class CalendarSyncResult:
    """Итог синка календаря (upsert-семантика: счётчики — обработанные записи)."""

    cbr: int = 0
    dividends: int = 0
    smartlab: int = 0
    errors: int = 0


def parse_ru_date(text: str) -> date | None:
    """Первая русская дата вида «13 февраля 2026 года» из текста; None — нет."""
    m = _DATE_RE.search(text)
    if not m:
        return None
    day, month_name, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
    try:
        return date(year, _MONTHS[month_name], day)
    except ValueError:
        return None


_MEETING_MARKERS = ("заседание совета директоров", "ключевой ставке")


def _is_meeting(text: str) -> bool:
    low = text.lower()
    return all(m in low for m in _MEETING_MARKERS)


def parse_cbr_calendar(html: str) -> list[date]:
    """Даты заседаний СД ЦБ по ключевой ставке из HTML страницы графика.

    Вёрстка (2026): вкладки по годам (активная — текущий), внутри — блоки
    `div.main-events_day` с датой в `.date` («13 февраля 2026 года») и
    событиями дня в `.main-event`. Берём дни, среди событий которых есть
    «Заседание Совета директоров … по ключевой ставке»; «Резюме обсуждения»
    идёт отдельным днём и заседанием не является. Фолбэк при смене вёрстки —
    проход по строкам таблиц (дата-строка предшествует строке события).
    Чистая функция.
    """
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)
    root = tree
    # Активная вкладка (текущий год); если вкладок нет — весь документ
    # (лишние годы отсечёт окно _CBR_WINDOW_DAYS в sync_cbr_calendar).
    active = tree.css_first("a.tab._active")
    if active is not None:
        tab_id = active.attributes.get("data-tabs-tab")
        panel = tree.css_first(f'[data-tabs-content="{tab_id}"]') if tab_id else None
        if panel is not None:
            root = panel

    meetings: list[date] = []
    for day in root.css("div.main-events_day"):
        date_node = day.css_first(".date")
        d = parse_ru_date(date_node.text(strip=True)) if date_node else None
        if d is None or d in meetings:
            continue
        if any(_is_meeting(ev.text(separator=" ", strip=True))
               for ev in day.css(".main-event")):
            meetings.append(d)
    if meetings:
        return meetings

    # Фолбэк: старая табличная вёрстка — дата и событие в соседних строках.
    current: date | None = None
    for tr in root.css("tr"):
        text = tr.text(separator=" ", strip=True)
        d = parse_ru_date(text)
        if d is not None:
            current = d
        if current and _is_meeting(text):
            if current not in meetings:
                meetings.append(current)
            current = None  # одна дата — одно заседание
    return meetings


def dividend_records(ticker: str, rows: list[dict]) -> list[dict]:
    """Записи отсечек из блока ISS dividends: [{event_date, value, currency}].

    `rows` — словари с ключами registryclosedate/value/currencyid (формат ISS).
    Битые даты пропускаются. Чистая функция.
    """
    out: list[dict] = []
    for r in rows:
        raw = r.get("registryclosedate")
        if not raw:
            continue
        try:
            d = date.fromisoformat(str(raw))
        except ValueError:
            continue
        out.append({"event_date": d, "value": r.get("value"),
                    "currency": r.get("currencyid")})
    return out


def _upsert(session: Session, *, kind: str, event_date: date, title: str,
            source: str, dedup_key: str, asset_id: int | None = None,
            payload: dict | None = None) -> None:
    stmt = pg_insert(CalendarEvent).values(
        kind=kind, event_date=event_date, asset_id=asset_id, title=title,
        source=source, dedup_key=dedup_key, payload=payload,
    ).on_conflict_do_update(
        constraint="uq_calendar_dedup",
        # Дата в ключе, но сумма дивиденда может уточняться после объявления.
        set_={"title": title, "payload": payload},
    )
    session.execute(stmt)


def sync_cbr_calendar(session: Session) -> int:
    """Затягивает график заседаний ЦБ по ставке. Возвращает число записей."""
    resp = httpx.get(CBR_CALENDAR_URL, timeout=60.0,
                     headers={"User-Agent": "geoanalytics/1.0"})
    resp.raise_for_status()
    meetings = parse_cbr_calendar(resp.text)
    today = datetime.now(UTC).date()
    window = timedelta(days=_CBR_WINDOW_DAYS)
    count = 0
    for d in meetings:
        if abs(d - today) > window:
            continue
        _upsert(
            session, kind=KIND_CBR, event_date=d,
            title="Заседание СД Банка России по ключевой ставке",
            source="cbr", dedup_key=f"{KIND_CBR}:CBR:{d.isoformat()}",
        )
        count += 1
    log.info("calendar_cbr_synced", meetings=count)
    return count


def sync_dividends(session: Session, *, pause_sec: float = 0.3) -> tuple[int, int]:
    """Затягивает отсечки по всем не-индексным активам. Возвращает (записей, ошибок).

    ISS отдаёт всю историю + объявленные будущие отсечки; пишем всё (история —
    якоря E1). Пауза между тикерами — троттлинг iss.moex.com (M0).
    """
    assets = session.execute(
        select(Asset.id, Asset.ticker).where(Asset.kind != "index")
    ).all()
    count = errors = 0
    for asset_id, ticker in assets:
        try:
            resp = httpx.get(ISS_DIVIDENDS_URL.format(ticker=ticker),
                             params={"iss.meta": "off"}, timeout=60.0)
            resp.raise_for_status()
            block = resp.json().get("dividends", {})
            cols = block.get("columns", [])
            rows = [dict(zip(cols, r, strict=False)) for r in block.get("data", [])]
        except Exception as exc:  # noqa: BLE001 — один тикер не валит синк
            errors += 1
            log.warning("calendar_dividends_failed", ticker=ticker, error=str(exc))
            continue
        for rec in dividend_records(ticker, rows):
            d = rec["event_date"]
            value = rec["value"]
            val_str = f" ({value} {rec['currency'] or 'RUB'})" if value is not None else ""
            _upsert(
                session, kind=KIND_DIVIDEND, event_date=d, asset_id=asset_id,
                title=f"Дивидендная отсечка {ticker}{val_str}",
                source="moex", dedup_key=f"{KIND_DIVIDEND}:{ticker}:{d.isoformat()}",
                payload={"value": value, "currency": rec["currency"]},
            )
            count += 1
        time.sleep(pause_sec)
    log.info("calendar_dividends_synced", records=count, errors=errors,
             tickers=len(assets))
    return count, errors


def parse_smartlab_dividends(html: str) -> list[dict]:
    """Объявленные дивиденды из таблицы smart-lab: [{ticker, event_date, value}].

    Колонки ищутся по заголовкам («Тикер», «Дивиденд, руб», «Дата закрытия
    реестра») — устойчиво к перестановке. Строки без даты отсечки (дивиденд
    рекомендован, дата не назначена) пропускаются. Чистая функция.
    """
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)
    out: list[dict] = []
    for table in tree.css("table"):
        headers = [th.text(separator=" ", strip=True).lower()
                   for th in table.css("th")]
        cols: dict[str, int] = {}
        for i, h in enumerate(headers):
            if h == "тикер":
                cols["ticker"] = i
            elif h.startswith("дивиденд"):
                cols["value"] = i
            elif "закрытия реестра" in h:
                cols["date"] = i
        if len(cols) < 3:
            continue
        for tr in table.css("tr"):
            tds = [td.text(separator=" ", strip=True) for td in tr.css("td")]
            if len(tds) <= max(cols.values()):
                continue
            ticker = tds[cols["ticker"]].strip().upper()
            if not ticker.isalpha():
                continue
            try:
                d = datetime.strptime(tds[cols["date"]].strip(), "%d.%m.%Y").date()
            except ValueError:
                continue
            try:
                value = float(tds[cols["value"]].replace(",", ".").replace(" ", ""))
            except ValueError:
                value = None
            out.append({"ticker": ticker, "event_date": d, "value": value})
    return out


def sync_smartlab_dividends(session: Session) -> int:
    """Затягивает объявленные отсечки smart-lab по известным активам."""
    resp = httpx.get(SMARTLAB_DIVIDENDS_URL, timeout=60.0, headers=_UA)
    resp.raise_for_status()
    records = parse_smartlab_dividends(resp.text)
    known = {t: aid for aid, t in session.execute(
        select(Asset.id, Asset.ticker).where(Asset.kind != "index")).all()}
    count = 0
    for rec in records:
        asset_id = known.get(rec["ticker"])
        if asset_id is None:
            continue
        ticker, d, value = rec["ticker"], rec["event_date"], rec["value"]
        val_str = f" ({value} RUB)" if value is not None else ""
        _upsert(
            session, kind=KIND_DIVIDEND, event_date=d, asset_id=asset_id,
            title=f"Дивидендная отсечка {ticker}{val_str}",
            source="smartlab", dedup_key=f"{KIND_DIVIDEND}:{ticker}:{d.isoformat()}",
            payload={"value": value, "currency": "RUB"},
        )
        count += 1
    log.info("calendar_smartlab_synced", records=count, parsed=len(records))
    return count


def sync_calendar() -> CalendarSyncResult:
    """Полный синк календаря (ЦБ + дивиденды), каждый источник в своём try."""
    from geoanalytics.storage.db import session_scope

    result = CalendarSyncResult()
    with session_scope() as session:
        try:
            result.cbr = sync_cbr_calendar(session)
        except Exception as exc:  # noqa: BLE001
            result.errors += 1
            log.error("calendar_cbr_sync_failed", error=str(exc))
    with session_scope() as session:
        try:
            divs, errs = sync_dividends(session)
            result.dividends = divs
            result.errors += errs
        except Exception as exc:  # noqa: BLE001
            result.errors += 1
            log.error("calendar_dividends_sync_failed", error=str(exc))
    with session_scope() as session:
        try:
            result.smartlab = sync_smartlab_dividends(session)
        except Exception as exc:  # noqa: BLE001
            result.errors += 1
            log.error("calendar_smartlab_sync_failed", error=str(exc))
    return result


def upcoming_events(session: Session, *, days_ahead: int = 1,
                    today: date | None = None) -> list[dict]:
    """События с event_date в [today; today+days_ahead] — срез для алертов/CLI.

    Возвращает [{kind, ticker, title, event_date, days_left, payload}];
    ticker None — макро-событие (ЦБ).
    """
    today = today or datetime.now(UTC).date()
    horizon = today + timedelta(days=days_ahead)
    rows = session.execute(
        select(CalendarEvent, Asset.ticker)
        .outerjoin(Asset, Asset.id == CalendarEvent.asset_id)
        .where(CalendarEvent.event_date >= today,
               CalendarEvent.event_date <= horizon)
        .order_by(CalendarEvent.event_date, CalendarEvent.kind)
    ).all()
    return [
        {"kind": ev.kind, "ticker": ticker, "title": ev.title,
         "event_date": ev.event_date, "days_left": (ev.event_date - today).days,
         "payload": ev.payload}
        for ev, ticker in rows
    ]
