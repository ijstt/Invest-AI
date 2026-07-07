"""Человекочитаемый рендеринг контекста объекта в grounding для LLM-нарратива.

Лёгкие/средние модели плохо читают сырой dict (`{'rsi14': 72, ...}`) — путаются в
числах, «не видят» поля. Поэтому контекст (drivers, собранные context/*_context.py)
превращается в структурированный РУССКИЙ текст секциями с интерпретацией значений
(зона RSI, позиция к средним, сила корреляции, жёсткость ДКП). Пустые секции
опускаются. Модуль ЧИСТЫЙ (вход — готовый dict, без БД) — основной предмет тестов.
"""

from __future__ import annotations


# --------------------------------------------------------------------------- #
# Чистые интерпретаторы значений.
# --------------------------------------------------------------------------- #
def rsi_zone(rsi: float) -> str:
    """Зона RSI(14)."""
    if rsi >= 70:
        return "перекупленность"
    if rsi <= 30:
        return "перепроданность"
    return "нейтрально"


def trend_text(t: dict) -> str:
    """Тренд словами по расположению цены относительно средних."""
    last, sma50, sma200 = t.get("last"), t.get("sma50"), t.get("sma200")
    base = {"up": "восходящий", "down": "нисходящий", "flat": "боковой"}.get(t.get("trend"), "—")
    if last is not None and sma50 is not None:
        pos = "выше" if last > sma50 else "ниже"
        tail = f" (цена {pos} SMA50"
        if sma200 is not None:
            tail += f", {'выше' if last > sma200 else 'ниже'} SMA200"
        return base + tail + ")"
    return base


def macd_text(t: dict) -> str | None:
    """MACD словами: бычий/медвежий кросс + сила гистограммы (или None, если данных нет)."""
    macd, signal = t.get("macd"), t.get("macd_signal")
    if macd is None or signal is None:
        return None
    cross = "бычий (MACD выше сигнальной)" if macd >= signal else "медвежий (MACD ниже сигнальной)"
    hist = t.get("macd_hist")
    tail = f", гистограмма {hist:+}" if hist is not None else ""
    return f"{cross}{tail}"


def bollinger_text(t: dict) -> str | None:
    """Положение цены в полосах Боллинджера (у верхней/нижней/в середине). None — нет данных."""
    last, lo, up = t.get("last"), t.get("boll_lower"), t.get("boll_upper")
    if last is None or lo is None or up is None or up <= lo:
        return None
    pos = (last - lo) / (up - lo)            # 0 — нижняя полоса, 1 — верхняя
    if pos >= 0.85:
        return "у верхней полосы (растяжение вверх/перекупленность)"
    if pos <= 0.15:
        return "у нижней полосы (растяжение вниз/перепроданность)"
    return "в середине канала"


def corr_strength(r: float) -> str:
    """Сила и направление корреляции."""
    a = abs(r)
    mag = "сильная" if a >= 0.5 else "умеренная" if a >= 0.2 else "слабая"
    direction = "прямая" if r >= 0 else "обратная"
    return f"{mag} {direction}"


def mood(sentiment: dict) -> str:
    """Преобладающее настроение по счётчикам тональности."""
    neg, neu, pos = (sentiment.get("negative", 0), sentiment.get("neutral", 0),
                     sentiment.get("positive", 0))
    if neg > neu + pos:
        return "преобладает негатив"
    if pos > neu + neg:
        return "преобладает позитив"
    return "смешанная/нейтральная"


def rate_stance(rate: float) -> str:
    """Характеристика жёсткости денежно-кредитной политики по ставке."""
    if rate >= 16:
        return "очень жёсткая ДКП"
    if rate >= 13:
        return "жёсткая ДКП"
    if rate >= 8:
        return "умеренная ДКП"
    return "мягкая ДКП"


# Человекочитаемые подписи корреляционных факторов.
_CORR_LABELS = {
    "usd_rub": "с USD/RUB", "usd_eur": "с USD/EUR", "brent": "с нефтью Brent",
    "gold": "с золотом", "silver": "с серебром", "platinum": "с платиной",
    "palladium": "с палладием", "sector_peers": "с пирами сектора",
}
# Человекочитаемые подписи внешних ставок.
_EXT_RATE_LABELS = {
    "fed_funds": "ставка ФРС США", "us_10y": "доходность 10y США",
    "ecb_dfr": "депозитная ставка ЕЦБ", "ecb_mrr": "ставка рефинансирования ЕЦБ",
}


# --------------------------------------------------------------------------- #
# Секции рендера (каждая возвращает список строк или []).
# --------------------------------------------------------------------------- #
def _section_technical(t: dict) -> list[str]:
    if not t:
        return []
    out = ["ТЕХНИЧЕСКИЙ АНАЛИЗ:"]
    if t.get("last") is not None:
        out.append(f"- Цена {t['last']}, тренд {trend_text(t)}.")
    if t.get("rsi14") is not None:
        out.append(f"- RSI(14) = {t['rsi14']} — {rsi_zone(t['rsi14'])}.")
    macd = macd_text(t)
    if macd:
        out.append(f"- MACD: {macd}.")
    boll = bollinger_text(t)
    if boll:
        out.append(f"- Боллинджер: цена {boll}.")
    rets = [(k, lbl) for k, lbl in (("ret_1w", "неделя"), ("ret_1m", "месяц"),
                                    ("ret_3m", "3 мес")) if t.get(k) is not None]
    if rets:
        out.append("- Доходность: " + ", ".join(f"{lbl} {t[k]:+}%" for k, lbl in rets) + ".")
    if t.get("vol_annual") is not None:
        out.append(f"- Волатильность годовая {t['vol_annual']}%.")
    if t.get("high_52w") is not None and t.get("low_52w") is not None:
        out.append(f"- Диапазон 52 недели: {t['low_52w']}–{t['high_52w']}.")
    return out


def _section_macro(m: dict) -> list[str]:
    if not m:
        return []
    out = ["МАКРО (РФ):"]
    if m.get("key_rate") is not None:
        out.append(f"- Ключевая ставка ЦБ {m['key_rate']}% — {rate_stance(m['key_rate'])}.")
    fx = m.get("fx") or {}
    if fx:
        out.append("- Курсы: " + ", ".join(f"{c} {v}" for c, v in fx.items()) + ".")
    com = m.get("commodities") or {}
    if com:
        names = {"brent": "Brent", "gold": "золото (₽/г)", "silver": "серебро (₽/г)",
                 "platinum": "платина (₽/г)", "palladium": "палладий (₽/г)"}
        out.append("- Сырьё: " + ", ".join(f"{names.get(k, k)} {v}" for k, v in com.items()) + ".")
    ext = m.get("external_rates") or {}
    if ext:
        out.append("- Внешние ставки: "
                   + ", ".join(f"{_EXT_RATE_LABELS.get(k, k)} {v}%" for k, v in ext.items()) + ".")
    return out


def _section_aggregate(agg: dict) -> list[str]:
    """Агрегат по активам сектора (breadth, средние). Только для sector-объекта."""
    if not agg or not agg.get("count"):
        return []
    out = [f"АГРЕГАТ СЕКТОРА ({agg['count']} компаний):"]
    if agg.get("avg_ret_1m") is not None:
        out.append(f"- Средняя доходность за месяц: {agg['avg_ret_1m']:+}%.")
    if agg.get("avg_rsi14") is not None:
        out.append(f"- Средний RSI(14): {agg['avg_rsi14']} — {rsi_zone(agg['avg_rsi14'])}.")
    out.append(f"- Динамика: растут {agg.get('breadth_up', 0)}, "
               f"падают {agg.get('breadth_down', 0)}.")
    return out


def _section_sentiment(s: dict) -> list[str]:
    """Тональный моментум и ширина настроения (B1) + дивергенция с ценой. Только при данных."""
    if not s or s.get("ewma") is None:
        return []
    ewma = s["ewma"]
    tone = "позитивный" if ewma > 0.1 else "негативный" if ewma < -0.1 else "нейтральный"
    out = ["НАСТРОЕНИЕ (тренд):", f"- Тональный моментум EWMA {ewma:+.2f} — {tone} фон."]
    if s.get("breadth") is not None:
        out.append(f"- Ширина настроения {s['breadth']:+.2f} (доля позитив − негатив).")
    if s.get("diverging"):
        out.append("- ДИВЕРГЕНЦИЯ: движение цены расходится с настроением новостей.")
    return out


def _section_stance(s: dict) -> list[str]:
    """C1: рекомендательная стойка — сигнал, уверенность и драйверы со знаком. При данных."""
    if not s or not s.get("signal"):
        return []
    out = [f"СТОЙКА (рекомендация): {s['signal']} — уверенность "
           f"{round((s.get('conviction') or 0) * 100)}%, балл {s.get('score', 0):+}."]
    if s.get("drivers"):
        out.append("- Драйверы: " + "; ".join(s["drivers"]) + ".")
    out.append("- Это образовательная аналитика, не индивидуальная инвестрекомендация.")
    return out


def _section_factors(f: dict) -> list[str]:
    if not f:
        return []
    out = ["СЕКТОР И ФАКТОРЫ:"]
    if f.get("sector"):
        out.append(f"- Сектор: {f['sector']}.")
    if f.get("macro_factors"):
        out.append("- Ключевые драйверы: " + ", ".join(f["macro_factors"]) + ".")
    if f.get("peers"):
        out.append("- Пиры сектора: " + ", ".join(f["peers"][:10]) + ".")
    return out


def _section_events(events: list[dict]) -> list[str]:
    if not events:
        return []
    out = ["СОБЫТИЯ (влияние на объект):"]
    for e in events[:5]:
        out.append(f"- [{e.get('type', '?')}, {e.get('direction', '?')}, сила "
                   f"{e.get('magnitude', 0)}] {e.get('title', '')}")
    return out


def _section_correlations(corr: dict) -> list[str]:
    if not corr:
        return []
    out = ["КОРРЕЛЯЦИИ:"]
    for k, v in list(corr.items())[:5]:
        if v is None:
            continue
        out.append(f"- {_CORR_LABELS.get(k, k)}: {v} ({corr_strength(v)}).")
    return out if len(out) > 1 else []


def _section_news(news: dict) -> list[str]:
    if not news or not news.get("recent_count"):
        return []
    sent = news.get("sentiment", {})
    out = ["НОВОСТНОЙ ФОН (неделя):",
           f"- {news['recent_count']} публикаций, тональность "
           f"+{sent.get('positive', 0)}/={sent.get('neutral', 0)}/−{sent.get('negative', 0)} "
           f"({mood(sent)})."]
    if news.get("top_events"):
        themes = ", ".join(f"{t}×{c}" for t, c in news["top_events"][:5])
        out.append(f"- Темы: {themes}.")
    return out


def _section_related(related: list[str]) -> list[str]:
    """Связанные объекты (briefs ≤1 строки) — кросс-объектный контекст."""
    briefs = [b for b in (related or []) if b]
    if not briefs:
        return []
    return ["СВЯЗАННЫЕ ОБЪЕКТЫ:"] + [f"- {b}" for b in briefs]


def render_grounding(drivers: dict, *, header: str | None = None,
                     related: list[str] | None = None) -> str:
    """Собирает grounding-текст из drivers. Пустые секции опускаются.

    `header` — строка «ОБЪЕКТ: …»; `related` — briefs связанных объектов (кросс-контекст).
    """
    lines: list[str] = []
    if header:
        lines += [header, ""]
    sections = [
        _section_technical(drivers.get("technical", {})),
        _section_sentiment(drivers.get("sentiment_trend", {})),
        _section_stance(drivers.get("stance", {})),
        _section_aggregate(drivers.get("aggregate", {})),
        _section_macro(drivers.get("macro", {})),
        _section_factors(drivers.get("factors", {})),
        _section_events(drivers.get("impacting_events", [])),
        _section_correlations(drivers.get("correlations", {})),
        _section_news(drivers.get("news", {})),
        _section_related(related or []),
    ]
    for sec in sections:
        if sec:
            lines += sec
            lines.append("")
    return "\n".join(lines).strip()
