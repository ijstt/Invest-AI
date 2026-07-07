"""Доставка алертов: console/log (всегда) + Telegram (если настроен).

Все каналы graceful: отсутствие конфигурации или сетевая ошибка не валят прогон —
алерт всё равно попадает в лог и в БД, а движок просто отметит фактические каналы.
"""

from __future__ import annotations

import html
import time

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config.settings import Settings
from geoanalytics.alerts.rules import Alert
from geoanalytics.core.logging import get_logger

log = get_logger("alerts.channels")

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_SEVERITY_ICON = {"info": "ℹ️", "warning": "⚠️", "critical": "🔴"}
# Эмодзи по типу события — мгновенно считываемый «жанр» алерта (цена/новость/событие/техника/…).
_TYPE_ICON = {"neg_spike": "📰", "new_event": "🗞", "combo": "🔥",
              "technical": "📊", "calendar": "📅", "portfolio": "💼", "scope": "🌐"}
# Человекочитаемая метка важности для строки-контекста.
_SEV_LABEL = {"info": "ℹ️ инфо", "warning": "⚠️ важно", "critical": "🔴 критично"}
# Пауза между отправками разным получателям, сек. Telegram троттлит залпы сообщений —
# небольшой зазор устраняет потери при рассылке списку.
_SEND_GAP_SEC = 0.4


def _type_icon(alert: Alert) -> str:
    """Эмодзи по типу алерта; для движения цены — направление из payload (📈/📉)."""
    if alert.alert_type == "price_move":
        return "📈" if (alert.payload or {}).get("change_pct", 0) >= 0 else "📉"
    return _TYPE_ICON.get(alert.alert_type, _SEVERITY_ICON.get(alert.severity, "🔔"))


def format_text(alert: Alert) -> str:
    """Plain-текст для консоли/лога (без разметки). Со ссылкой-первоисточником, если есть."""
    icon = _type_icon(alert)
    out = f"{icon} {alert.title}\n{alert.message}".strip()
    url = (alert.payload or {}).get("url")
    if url:
        out += f"\n🔗 {url}"
    return out


def format_telegram(alert: Alert) -> str:
    """HTML-уведомление для Telegram: жирный заголовок с эмодзи-типом, тело, строка-контекст
    (актив · важность) и кликабельная ссылка-источник. Всё экранируется (parse_mode=HTML)."""
    icon = _type_icon(alert)
    lines = [f"<b>{icon} {html.escape(alert.title)}</b>"]
    if alert.message:
        lines.append(html.escape(alert.message))
    ctx = []
    if alert.ticker:
        ctx.append(f"🎯 {html.escape(alert.ticker)}")
    ctx.append(_SEV_LABEL.get(alert.severity, html.escape(alert.severity)))
    lines.append(" · ".join(ctx))
    url = (alert.payload or {}).get("url")
    if url:
        lines.append(f'🔗 <a href="{html.escape(url, quote=True)}">Источник</a>')
    return "\n".join(lines)


@retry(
    retry=retry_if_exception_type(httpx.TransportError),
    stop=stop_after_attempt(5),
    wait=wait_exponential(min=2, max=10),
    reraise=True,
)
def _post_telegram(token: str, chat_id: str, text: str, timeout: float,
                   proxy: str | None = None, parse_mode: str | None = None) -> httpx.Response:
    """POST в Telegram с ретраями на транспортные сбои (SSL EOF/таймаут/connect).

    Ретраим только сетевой слой (`httpx.TransportError`) — наблюдался разовый
    `[SSL: UNEXPECTED_EOF]` при залпе. HTTP-ошибки (например, «chat not found») НЕ
    ретраим: они детерминированы и повтор бессмыслен. `proxy` (SOCKS/HTTP) — только для
    Telegram (split-tunnel на Pi, где Telegram заблокирован); None — прямое соединение.
    `parse_mode` (HTML по умолчанию) — разметка жирного/ссылок; None — plain.
    """
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    with httpx.Client(proxy=proxy, timeout=timeout) as client:
        resp = client.post(_TELEGRAM_API.format(token=token), json=payload)
    resp.raise_for_status()
    return resp


def send_telegram(token: str, chat_id: str, text: str, *, timeout: float = 10.0,
                  proxy: str | None = None, parse_mode: str | None = None) -> bool:
    """Отправляет сообщение через Telegram Bot API. False при любой ошибке.

    `parse_mode=None` по умолчанию (plain) — БЕЗОПАСНО для ответов бота с сырыми `<`/`>`/`&`
    (заголовки новостей). Алерты передают `parse_mode="HTML"` с уже экранированным текстом."""
    try:
        _post_telegram(token, chat_id, text, timeout, proxy=proxy, parse_mode=parse_mode)
        return True
    except httpx.HTTPStatusError as exc:
        # Текст Telegram (например, "chat not found" — бот не получал /start от чата).
        try:
            detail = exc.response.json().get("description", exc.response.text)
        except Exception:
            detail = exc.response.text
        log.warning("telegram_send_failed", status=exc.response.status_code, detail=detail)
        return False
    except Exception as exc:  # сеть/таймаут — не валим прогон
        log.warning("telegram_send_failed", error=str(exc))
        return False


def dispatch(alert: Alert, settings: Settings,
             *, chat_ids: list[str] | None = None) -> list[str]:
    """Доставляет алерт по всем активным каналам. Возвращает имена доставленных.

    console — всегда (виден в логе scheduler/CLI). Telegram — если задан токен и хотя бы
    один chat_id; рассылается всем получателям (с паузой против троттлинга), канал
    считается доставленным, если ушло хотя бы одному. `chat_ids` (5b) — явный список
    получателей (allowed-пользователи с учётом личных mute); None — старый allowlist настроек.
    """
    delivered = ["console"]
    log.info("alert", type=alert.alert_type, ticker=alert.ticker,
             severity=alert.severity, title=alert.title)  # plain `format_text` — в лог/консоль

    chat_ids = settings.telegram_chat_ids if chat_ids is None else chat_ids
    if settings.telegram_bot_token and chat_ids:
        html_text = format_telegram(alert)            # HTML-вид только для Telegram
        sent_any = False
        for i, chat_id in enumerate(chat_ids):
            if i:  # пауза только между отправками, не перед первой/после последней
                time.sleep(_SEND_GAP_SEC)
            if send_telegram(settings.telegram_bot_token, chat_id, html_text,
                             proxy=settings.telegram_proxy, parse_mode="HTML"):
                sent_any = True
        if sent_any:
            delivered.append("telegram")
    return delivered
