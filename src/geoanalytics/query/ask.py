"""Текстовый интерфейс к аналитике (RAG-оркестратор).

Свободный вопрос пользователя → ответ поверх УЖЕ готовых query-функций. Поток:
1. **Интент**: LLM (`nlp.llm.generate`) по строгому промпту возвращает JSON
   `{intent, ticker, hours, strategy, query}`; чистый `_parse_intent` его разбирает.
2. **Диспатч**: интент маршрутизируется в существующее — `asset_report.build_report`,
   `news_summary.build_snapshot`, `analytics.backtest.backtest_asset`,
   `events_feed.recent_events`, `alerts_feed.recent_alerts`, либо `semantic.search_news`.
3. **Нарратив**: короткий заземлённый ответ (LLM по собранным фактам) + цитаты-ссылки.
4. **Graceful-фолбэк** (как везде в проекте): LLM недоступен/интент неясен → эвристика
   (тикер по `EntityIndex`, ключевые слова). Чистые функции тестируются без БД/сети.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select

from config.settings import get_settings
from geoanalytics.analytics.backtest import PRICE_STRATEGIES, backtest_asset_cached
from geoanalytics.context.country_context import build_country_context
from geoanalytics.context.grounding import render_grounding
from geoanalytics.context.sector_context import build_sector_context
from geoanalytics.core.locks import LLMBusy, llm_generation_lock
from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import EntityType
from geoanalytics.nlp import llm
from geoanalytics.nlp.entity_linking import EntityIndex
from geoanalytics.query import semantic
from geoanalytics.query.alerts_feed import recent_alerts
from geoanalytics.query.asset_report import build_report
from geoanalytics.query.events_feed import recent_events
from geoanalytics.query.news_summary import build_snapshot
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import Asset, Country, Sector

log = get_logger("query.ask")


def _router_llm(prompt: str, **opts) -> str | None:
    """LLM-вызов ask-пути на ЛЁГКОЙ модели (роутер-интент + короткий нарратив).

    Экономия RAM на слабом железе: вместо 7B (~5 ГБ, главный источник перегрузки
    памяти при работе RAG) — маленькая модель с урезанным контекстом и keep_alive.
    `opts` (repeat_penalty/stop/num_predict) пробрасываются в `llm.generate` — нарратив
    задаёт их против зацикливания. Параметры модели в настройках (`llm_router_*`); при
    недоступности модели — None (выше включается graceful-фолбэк на эвристику/шаблон).
    """
    s = get_settings()
    return llm.generate(
        prompt,
        model=s.llm_router_model,
        num_ctx=s.llm_router_num_ctx,
        keep_alive=s.llm_router_keep_alive,
        **opts,
    )


ALLOWED_INTENTS = {"asset", "market", "backtest", "events", "alerts", "news",
                   "portfolio", "recommend", "help"}
_VALID_STRATEGIES = {*PRICE_STRATEGIES, "sentiment"}

# Трек B: «что купить/докупить/вложить/посоветуй» → скринер идей (recommend). Широкие стемы;
# в фолбэке гейтятся «без тикера» (с тикером «купить SBER?» → анализ актива, а не подбор).
_RECOMMEND_KW = ("купить", "докупить", "докуп", "вложить", "вложен", "инвестир", "что брать",
                 "посоветуй", "порекомендуй", "что взять", "идеи для покупк", "что добавить",
                 "приобрести", "во что", "куда влож")
# Трек B: «помоги/как разобраться/с чего начать/что ты умеешь» → дружелюбное меню (help).
_HELP_KW = ("помоги", "помощь", "как разобрат", "с чего нач", "что ты умеешь", "что умеешь",
            "не понимаю", "как пользоват", "что мне делать", "не знаю")
# Явный новостной вопрос — чтобы open-ended фолбэк шёл в help, а не молча в поиск новостей.
_NEWS_KW = ("новост", "что нов", "почему", "что случилось", "что происходит", "что там с",
            "слышал", "пишут")
_MARKET_KW = ("рынок", "рынк", "сводк", "что по новост", "обзор", "настроени")
_ALERT_KW = ("алерт", "уведомлен", "сигнал", "триггер")
_EVENT_KW = ("событи", "санкци", "дивиденд", "отчётност", "отчетност", "геополит")
_BACKTEST_KW = ("бэктест", "backtest", "стратеги", "протестир")
# Волна 6 (б): вопросы про ВЕСЬ портфель владельца (риск/экспозиция/аллокация/сценарий).
_PORTFOLIO_KW = ("портфел", "мои позиц", "моих позиц", "моя экспозиц", "мой риск",
                 "у меня в портфел", "что у меня")
# NL-факторы для сценарных шоков (что-если): слово в вопросе → фактор атрибуции.
_FACTOR_NL = {"brent": ("нефт", "brent", "брент"),
              "usd_rub": ("рубл", "доллар", "usd", "валют"),
              "gold": ("золот",), "silver": ("серебр",)}


@dataclass
class AskResult:
    question: str
    intent: str                                   # asset|market|backtest|events|alerts|news|unknown
    answer: str                                   # связный ответ (LLM) или шаблонный фолбэк
    facts: list[str] = field(default_factory=list)     # ключевые факты для показа
    citations: list[dict] = field(default_factory=list)  # [{title, url}]
    ticker: str | None = None
    used_llm: bool = False
    note: str = ""
    portfolio: dict | None = None                 # Волна 6: контекст актива в портфеле владельца
    rag_trace: list[dict] = field(default_factory=list)  # Волна 6: RAG-трейс [{title,url,score}]
    recommendations: list[dict] = field(default_factory=list)  # Трек B: идеи скринера (recommend)


# --------------------------------------------------------------------------- #
# Разбор интента (чистые функции).
# --------------------------------------------------------------------------- #
def _extract_json(text: str) -> str | None:
    """Первый сбалансированный объект `{...}` из текста LLM (она любит добавлять болтовню)."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _parse_intent(text: str | None) -> dict | None:
    """Текст LLM → нормализованный интент или None (невалидный JSON/интент)."""
    raw = _extract_json(text or "")
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict) or obj.get("intent") not in ALLOWED_INTENTS:
        return None
    ticker = obj.get("ticker")
    hours = obj.get("hours")
    strategy = obj.get("strategy")
    query = obj.get("query")
    return {
        "intent": obj["intent"],
        "ticker": str(ticker).upper() if ticker else None,
        "hours": hours if isinstance(hours, int) and hours > 0 else None,
        "strategy": strategy if strategy in _VALID_STRATEGIES else None,
        "query": str(query) if query else None,
    }


def _build_intent_prompt(question: str) -> str:
    return (
        "Ты — маршрутизатор запросов аналитической системы по рынку РФ. "
        "Определи намерение и верни СТРОГО JSON без пояснений и текста вокруг:\n"
        '{"intent": "asset|market|backtest|events|alerts|news|portfolio|recommend|help", '
        '"ticker": "<тикер MOEX или null>", "hours": <целое или null>, '
        '"strategy": "sma_cross|momentum|rsi|sentiment или null", '
        '"query": "<поисковый запрос для news или null>"}\n\n'
        "Намерения:\n"
        "- asset: вопрос про конкретный актив/компанию (заполни ticker);\n"
        "- market: общая сводка рынка, «что по новостям»;\n"
        "- backtest: тест стратегии на активе (ticker + strategy);\n"
        "- events: события (санкции, дивиденды, отчётность, геополитика);\n"
        "- alerts: сработавшие алерты/уведомления;\n"
        "- portfolio: вопрос про ВЕСЬ портфель пользователя — риск, экспозиция, "
        "состав/аллокация, сценарий «что если» (нефть/рубль/золото ±N%);\n"
        "- recommend: «что мне купить/докупить/куда вложить, посоветуй идеи» — подбор активов;\n"
        "- help: непонятный/общий вопрос, «помоги, как разобраться, с чего начать»;\n"
        "- news: открытый вопрос по новостям (заполни query).\n\n"
        f"Вопрос: {question}\nJSON:"
    )


def _fallback_intent(question: str, ticker: str | None) -> dict:
    """Эвристика, когда LLM недоступен/невнятен. Чистая (тикер передаётся снаружи)."""
    q = question.lower()
    base = {"intent": "news", "ticker": ticker, "hours": None,
            "strategy": None, "query": question}
    if any(k in q for k in _BACKTEST_KW) and ticker:
        return {**base, "intent": "backtest", "strategy": "sma_cross", "query": None}
    # Трек B: «что купить/докупить/вложить/посоветуй» БЕЗ конкретного тикера → скринер идей
    # (recommend). С тикером («стоит купить SBER?») — это анализ актива, обрабатывается ниже.
    if not ticker and any(k in q for k in _RECOMMEND_KW):
        return {**base, "intent": "recommend", "query": None}
    # Портфель-интент (б): явные «портфельные» слова ИЛИ распознанный сценарий-шок.
    if any(k in q for k in _PORTFOLIO_KW) or _parse_shock(question):
        return {**base, "intent": "portfolio", "query": None}
    if any(k in q for k in _ALERT_KW):
        return {**base, "intent": "alerts", "query": None}
    if any(k in q for k in _HELP_KW):
        return {**base, "intent": "help", "query": None}
    if ticker:
        return {**base, "intent": "asset", "query": None}
    if any(k in q for k in _MARKET_KW):
        return {**base, "intent": "market", "query": None}
    if any(k in q for k in _EVENT_KW):
        return {**base, "intent": "events", "query": None}
    if any(k in q for k in _NEWS_KW):
        return base                                 # явный новостной вопрос → семантический поиск
    return {**base, "intent": "help", "query": None}  # Трек B: непонятный вопрос → дружелюбное меню


# --------------------------------------------------------------------------- #
# Резолв тикера (фолбэк) и интента.
# --------------------------------------------------------------------------- #
def _match_ticker(session, question: str) -> str | None:
    """Лучший тикер из текста через EntityIndex (то же, чем линкуются статьи)."""
    links = EntityIndex(session).match(question)
    assets = [link for link in links if link.entity_type == EntityType.ASSET]
    if not assets:
        return None
    best = max(assets, key=lambda link: link.relevance)
    asset = session.get(Asset, best.entity_id)
    return asset.ticker if asset else None


def _resolve_ticker(question: str) -> str | None:
    """EntityIndex-резолв тикера по вопросу (для фолбэк-эвристики, без LLM)."""
    try:
        with session_scope() as session:
            return _match_ticker(session, question)
    except Exception as exc:  # noqa: BLE001 — фолбэк не должен падать
        log.warning("resolve_ticker_failed", error=str(exc))
        return None


def _verify_or_resolve_ticker(ticker: str | None, question: str) -> str | None:
    """Тикер вопроса: EntityIndex по тексту имеет ПРИОРИТЕТ над тикером от LLM.

    LLM выдаёт не только несуществующие коды («ROSNEFT» из «роснефти»), но и валидный,
    но НЕ ТОТ тикер (на «Северсталь» отдавал NVTK — он есть в БД, и старая проверка его
    принимала). EntityIndex привязан к тексту вопроса (леммы ловят склонения) и на golden-
    наборе точнее LLM, поэтому сперва матчим по тексту; тикер LLM — фолбэк, и то лишь если
    он реально есть в БД."""
    try:
        with session_scope() as session:
            matched = _match_ticker(session, question)
            if matched:
                return matched
            if ticker and session.scalars(
                    select(Asset).where(Asset.ticker == ticker)).first():
                return ticker
            return ticker
    except Exception as exc:  # noqa: BLE001 — резолв не должен ронять ответ
        log.warning("verify_ticker_failed", error=str(exc))
        return ticker


def resolve_object(question: str,
                   llm_ticker: str | None = None) -> tuple[str | None, int | None, str | None]:
    """Определяет ОБЪЕКТ вопроса через EntityIndex: (object_type, id, name|ticker).

    Приоритет ASSET > SECTOR > COUNTRY (актив конкретнее сектора, сектор — страны).
    Валидный тикер от LLM бьёт всё. Используется для маршрутизации вопроса в анализ
    актива/сектора/страны. (None, None, None) — объект не распознан.
    """
    try:
        with session_scope() as session:
            if llm_ticker:
                a = session.scalars(select(Asset).where(Asset.ticker == llm_ticker)).first()
                if a:
                    return "asset", a.id, a.ticker
            by_type: dict[EntityType, object] = {}
            for link in EntityIndex(session).match(question):
                cur = by_type.get(link.entity_type)
                if cur is None or link.relevance > cur.relevance:
                    by_type[link.entity_type] = link
            if (al := by_type.get(EntityType.ASSET)) and (a := session.get(Asset, al.entity_id)):
                return "asset", a.id, a.ticker
            if (sl := by_type.get(EntityType.SECTOR)) and (s := session.get(Sector, sl.entity_id)):
                return "sector", s.id, s.name
            cl = by_type.get(EntityType.COUNTRY)
            if cl and (c := session.get(Country, cl.entity_id)):
                return "country", c.id, c.name
    except Exception as exc:  # noqa: BLE001 — резолв не должен ронять ответ
        log.warning("resolve_object_failed", error=str(exc))
    return None, None, None


def resolve_intent(question: str, *, use_llm: bool = True) -> tuple[dict, bool]:
    """Интент вопроса. Возвращает (intent_obj, использован_ли_LLM).

    Для объектных интентов (asset/market) дополнительно определяет object_type
    (asset/sector/country) — это позволяет анализировать не только активы.
    """
    if use_llm:
        # Интент = короткий JSON: малый num_predict + почти нулевая temperature (строгость).
        raw = _router_llm(_build_intent_prompt(question), temperature=0.1, num_predict=120)
        parsed = _parse_intent(raw)
        if parsed:
            # LLM часто отдаёт неверный тикер (имя вместо MOEX-кода) — валидируем по БД.
            # Только там, где тикер нужен/задан, чтобы не дёргать EntityIndex зря.
            if parsed["intent"] in ("asset", "backtest") or parsed["ticker"]:
                parsed["ticker"] = _verify_or_resolve_ticker(parsed["ticker"], question)
            _attach_object(parsed, question)
            return parsed, True
    fb = _fallback_intent(question, _resolve_ticker(question))
    _attach_object(fb, question)
    return fb, False


def _attach_object(obj: dict, question: str) -> None:
    """Проставляет object_type/object_id/object_name для объектных интентов.

    Если вопрос про сектор/страну (а не конкретный актив) — переключает обработку на
    соответствующий анализ. Для backtest/events/alerts (действия) объект не нужен.
    """
    obj.setdefault("object_type", None)
    obj.setdefault("object_id", None)
    obj.setdefault("object_name", None)
    if obj["intent"] not in ("asset", "market"):
        return
    otype, oid, oname = resolve_object(question, obj.get("ticker"))
    if otype:
        obj["object_type"], obj["object_id"], obj["object_name"] = otype, oid, oname
        if otype == "asset":
            obj["ticker"] = oname


# --------------------------------------------------------------------------- #
# Диспатч в готовые query-функции → (факты, цитаты, грунт для нарратива).
# --------------------------------------------------------------------------- #
def _cite(items: list[dict]) -> list[dict]:
    """Уникальные цитаты {title, url, score?} из словарей с url. `score` (косинусная близость
    семантического поиска) пробрасывается, если есть — для RAG-трейса (Волна 6); шаблон
    «Источников» его игнорирует."""
    out, seen = [], set()
    for it in items:
        url = it.get("url")
        if url and url not in seen:
            seen.add(url)
            out.append({"title": it["title"], "url": url, "score": it.get("score")})
    return out


def _rag_trace(citations: list[dict]) -> list[dict]:
    """RAG-трейс (Волна 6): цитаты с реальным скором семантического поиска — прозрачность,
    что и насколько релевантно подтянул вектор-поиск. Пусто для структурных интентов
    (asset/market — там не вектор-RAG, а адресные запросы)."""
    return [{"title": c["title"], "url": c["url"], "score": c["score"]}
            for c in citations if c.get("score") is not None]


def _parse_shock(question: str) -> dict[str, float] | None:
    """NL-сценарий «что если фактор ±N%» → {фактор: shock%} (Волна 6б). Триггер — слово
    сценария + процент + узнаваемый фактор. Направление по словам (упадёт/вырастет), иначе
    отрицательное (риск-фрейм). None — не сценарный вопрос."""
    import re

    q = (question or "").lower()
    if not any(w in q for w in ("если", "сценари", "что будет")):
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", q)
    if not m:
        return None
    mag = float(m.group(1).replace(",", "."))
    pos = any(w in q for w in ("выраст", "рост", "подним", "укреп", "плюс"))
    neg = any(w in q for w in ("упад", "сниз", "падени", "обвал", "минус", "ослаб", "дешев"))
    sign = 1.0 if (pos and not neg) else -1.0
    shocks = {f: round(mag * sign, 2) for f, kws in _FACTOR_NL.items()
              if any(k in q for k in kws)}
    return shocks or None


def _dispatch_portfolio(question: str, user_id: int | None = None,
                        ) -> tuple[list[str], list[dict], str]:
    """Аналитика портфеля ПОЛЬЗОВАТЕЛЯ на естественном языке (Волна 6б): сводка риска/экспозиции/
    аллокации ИЛИ сценарий «что если» (мост к `whatif_portfolio`). Кэшированный отчёт по
    `user_id` (None — владелец)."""
    rep = owner_portfolio_report(user_id)
    if rep is None or rep.error:
        return ["Портфель пуст или недоступен."], [], ""

    shocks = _parse_shock(question)
    if shocks:
        try:
            from geoanalytics.analytics.whatif import whatif_portfolio
            from geoanalytics.storage.db import session_scope

            with session_scope() as session:
                sc = whatif_portfolio(session, shocks, user_id=user_id)
        except Exception as exc:                   # noqa: BLE001 — сценарий не критичен
            log.warning("ask_whatif_failed", error=str(exc))
            sc = None
        if sc and not sc.error and sc.portfolio_move_pct is not None:
            shk = ", ".join(f"{k} {v:+g}%" for k, v in shocks.items())
            facts = [f"Сценарий ({shk}) → портфель {sc.portfolio_move_pct:+.2f}%"]
            if sc.portfolio_pnl_rub is not None:
                facts.append(f"P&L: {sc.portfolio_pnl_rub:+,.0f} ₽")
            movers = sorted(sc.assets, key=lambda a: abs(a.expected_move_pct),
                            reverse=True)[:3]
            for a in movers:
                facts.append(f"{a.ticker}: {a.expected_move_pct:+.2f}%")
            grounding = ("Сценарный анализ портфеля (линейный по бетам). "
                         + "; ".join(facts) + ". " + " ".join(sc.caveats[:2]))
            return facts, [], grounding
        return ["Сценарий не рассчитан (нет бет по фактору или пустой портфель)."], [], ""

    # Сводка портфеля.
    facts = [f"Стоимость портфеля: {rep.total_value_rub:,.0f} ₽"]
    if rep.daily_vol_pct is not None:
        facts.append(f"дневная волатильность {rep.daily_vol_pct:.2f}%")
    if rep.var95_1d_pct is not None and rep.var95_1d_rub is not None:
        facts.append(f"VaR 95% (1д): {rep.var95_1d_pct:.2f}% ({rep.var95_1d_rub:,.0f} ₽)")
    if rep.max_drawdown_pct is not None:
        facts.append(f"макс. просадка: {rep.max_drawdown_pct:.2f}%")
    top = sorted((p for p in rep.positions if p.weight_pct),
                 key=lambda p: p.weight_pct, reverse=True)[:3]
    if top:
        facts.append("крупнейшие: " + ", ".join(f"{p.ticker} {p.weight_pct:.0f}%" for p in top))
    if rep.exposure:
        exp = sorted(rep.exposure.items(), key=lambda kv: abs(kv[1]), reverse=True)[:3]
        facts.append("экспозиция: " + ", ".join(f"{f} {b:+.2f}" for f, b in exp))
    if rep.sector_alloc:
        facts.append("секторы: " + ", ".join(f"{s} {w:.0f}%" for s, w in rep.sector_alloc[:3]))
    grounding = ("Портфель владельца. " + ". ".join(facts)
                 + f". Режим рынка: {rep.regime or 'н/д'}.")
    return facts, [], grounding


def _dispatch_sector(obj: dict) -> tuple[list[str], list[dict], str]:
    """Анализ отрасли/сектора: агрегат по активам + макро-драйверы + события + новости."""
    sid, sname = obj["object_id"], obj["object_name"]
    with session_scope() as session:
        drivers = build_sector_context(session, sid, sname)
    agg = drivers["aggregate"]
    facts = [f"Сектор «{sname}» ({agg.get('count', 0)} компаний)"]
    if agg.get("avg_ret_1m") is not None:
        facts.append(f"средняя доходность 1м: {agg['avg_ret_1m']:+}%")
    facts.append(f"растут {agg.get('breadth_up', 0)}/падают {agg.get('breadth_down', 0)}")
    grounding = render_grounding(drivers, header=f"ОБЪЕКТ: отрасль «{sname}», рынок РФ.")
    return facts, [], grounding


def _dispatch_country(obj: dict) -> tuple[list[str], list[dict], str]:
    """Анализ страны/экономики: макро + рынок + секторный срез + геополитика (кросс-связи)."""
    cid, cname = obj["object_id"], obj["object_name"]
    with session_scope() as session:
        drivers, related = build_country_context(session, cid, cname)
    m = drivers.get("macro", {})
    facts = [f"Экономика: {cname}"]
    if m.get("key_rate") is not None and cname == "Россия":
        facts.append(f"ключевая ставка ЦБ: {m['key_rate']}%")
    grounding = render_grounding(drivers, header=f"ОБЪЕКТ: экономика — {cname}.",
                                 related=related)
    return facts, [], grounding


def _dispatch(obj: dict, question: str,
              user_id: int | None = None) -> tuple[list[str], list[dict], str]:
    intent, ticker = obj["intent"], obj.get("ticker")

    if intent == "portfolio":
        return _dispatch_portfolio(question, user_id)

    # Объектная маршрутизация: вопрос может быть про сектор/страну, а не актив.
    if obj.get("object_type") == "sector" and obj.get("object_id"):
        return _dispatch_sector(obj)
    if obj.get("object_type") == "country" and obj.get("object_id"):
        return _dispatch_country(obj)

    if intent == "asset":
        if not ticker:
            return ["Не удалось определить тикер в вопросе."], [], ""
        r = build_report(ticker, rebuild=False, use_llm=False)
        if not r.found:
            return [f"Актив {ticker} не найден."], [], ""
        ind = r.indicators
        facts = [f"{r.name} ({r.ticker})"]
        for key, label in (("last", "цена"), ("trend", "тренд"), ("rsi14", "RSI(14)"),
                           ("ret_1m", "доходность 1м, %")):
            if ind.get(key) is not None:
                facts.append(f"{label}: {ind[key]}")
        if r.stance:                                   # C1: рекомендательная стойка
            st = r.stance
            facts.append(f"стойка: {st['label']} (уверенность "
                         f"{round(st['conviction'] * 100)}%, балл {st['score']:+})")
        cites = _cite(r.news[:6])
        # Богатый grounding: ВСЕ собранные данные (техника/макро/сектор/события/корреляции/
        # новостной фон) в человекочитаемом виде — раньше LLM получал лишь сырой dict.
        sent_c = Counter(n.get("sentiment") or "neutral" for n in r.news)
        ev_c = Counter(n["event_type"] for n in r.news if n.get("event_type"))
        drivers = {
            "technical": r.indicators, "macro": r.macro, "factors": r.factors,
            "correlations": r.correlations, "impacting_events": r.events,
            "news": {"recent_count": len(r.news), "sentiment": dict(sent_c),
                     "top_events": ev_c.most_common(5)},
        }
        if r.stance:                                   # C1: стойка с драйверами в grounding
            st = r.stance
            drivers["stance"] = {
                "signal": st["label"], "conviction": st["conviction"], "score": st["score"],
                "drivers": [f"{d['label']} {d['contribution']:+}" for d in st["drivers"]],
            }
        sector = r.factors.get("sector") or r.sector
        header = (f"ОБЪЕКТ: {r.name} ({r.ticker})"
                  + (f", сектор «{sector}»" if sector else "") + ", рынок РФ.")
        # Кросс-объектный контекст (1 хоп, briefs): актив зависит от сектора и страны.
        related = []
        if sector:
            drv = ", ".join(r.factors.get("macro_factors", [])[:3])
            related.append(f"Сектор «{sector}» — ключевые драйверы: {drv}.")
        if r.macro.get("key_rate") is not None:
            related.append(f"Экономика РФ — ключевая ставка ЦБ {r.macro['key_rate']}%.")
        grounding = render_grounding(drivers, header=header, related=related)
        return facts, cites, grounding

    if intent == "backtest":
        if not ticker:
            return ["Не удалось определить тикер для бэктеста."], [], ""
        strategy = obj.get("strategy") or "sma_cross"
        try:
            res = backtest_asset_cached(ticker, strategy=strategy)
        except ValueError as exc:
            return [str(exc)], [], ""
        if res is None:
            return [f"Актив {ticker} не найден."], [], ""
        facts = [
            f"{ticker} · {strategy}",
            f"доходность: {res.total_return_pct:+.2f}% (buy&hold {res.buy_hold_return_pct:+.2f}%)",
            f"Шарп: {res.sharpe:.2f}" if res.sharpe is not None else "Шарп: —",
            f"сделок: {res.num_trades}",
        ]
        grounding = (f"Бэктест {ticker} стратегией {strategy}: доходность "
                     f"{res.total_return_pct:+.2f}%, buy&hold {res.buy_hold_return_pct:+.2f}%, "
                     f"Шарп {res.sharpe}, сделок {res.num_trades}, экспозиция {res.exposure}.")
        return facts, [], grounding

    if intent == "market":
        snap = build_snapshot(hours=obj.get("hours") or 24, use_llm=False)
        sb = snap.sentiment_breakdown
        facts = []
        if snap.key_rate is not None:
            facts.append(f"ключевая ставка: {snap.key_rate}%")
        facts.append(f"тональность +{sb.get('positive', 0)}/={sb.get('neutral', 0)}/"
                     f"−{sb.get('negative', 0)}")
        if snap.top_gainers:
            facts.append("растут: " + ", ".join(m["ticker"] for m in snap.top_gainers[:3]))
        if snap.top_losers:
            facts.append("падают: " + ", ".join(m["ticker"] for m in snap.top_losers[:3]))
        cites = _cite(snap.headlines[:6])
        titles = "; ".join(h["title"] for h in snap.headlines[:6])
        # Человекочитаемый грунт (НЕ сырой dict): лёгкая модель иначе «не видит» ставку
        # и неверно читает тональность (путает преобладание негатива с нейтралом).
        neg, neu, pos = sb.get("negative", 0), sb.get("neutral", 0), sb.get("positive", 0)
        mood = ("преобладает негатив" if neg > neu + pos
                else "преобладает позитив" if pos > neu + neg
                else "смешанная/нейтральная")
        rate_txt = (f"ключевая ставка ЦБ РФ {snap.key_rate}%"
                    if snap.key_rate is not None else "ставка ЦБ неизвестна")
        gainers = ", ".join(m["ticker"] for m in snap.top_gainers[:3]) or "—"
        losers = ", ".join(m["ticker"] for m in snap.top_losers[:3]) or "—"
        grounding = (f"{rate_txt}. Тональность новостей за период: негативных {neg}, "
                     f"нейтральных {neu}, позитивных {pos} ({mood}). "
                     f"Растут: {gainers}. Падают: {losers}. Заголовки: {titles}")
        return facts, cites, grounding

    if intent == "events":
        evs = recent_events(hours=obj.get("hours") or 168, limit=10)
        if not evs:
            return ["Значимых событий за период нет."], [], ""
        facts = [f"[{e['event_type']}] {e['title']}" for e in evs[:6]]
        grounding = "События: " + "; ".join(f"{e['event_type']}: {e['title']}" for e in evs[:8])
        return facts, [], grounding

    if intent == "alerts":
        al = recent_alerts(hours=obj.get("hours") or 168, limit=10, ticker=ticker)
        if not al:
            return ["Сработавших алертов за период нет."], [], ""
        facts = [f"[{a['severity']}] {a['title']}" for a in al[:6]]
        grounding = "Алерты: " + "; ".join(a["title"] for a in al[:8])
        return facts, [], grounding

    # news (открытый вопрос) → семантический поиск
    query = obj.get("query") or question
    results = semantic.search_news(query, k=8, hours=obj.get("hours"))
    if not results:
        return (["Семантический поиск недоступен или релевантных новостей нет."], [], "")
    facts = [f"{r['title']} (score {r['score']})" for r in results[:6]]
    cites = _cite(results)
    titles = "; ".join(r["title"] for r in results[:8])
    grounding = f"Релевантные новости по запросу «{query}»: {titles}"
    return facts, cites, grounding


# --------------------------------------------------------------------------- #
# Нарратив и публичная точка входа.
# --------------------------------------------------------------------------- #
# Системная роль (chat API) — главный рычаг дисциплины языка/формата у Qwen.
_NARRATIVE_SYSTEM = (
    "Ты — финансовый аналитик рынка РФ. Отвечай ИСКЛЮЧИТЕЛЬНО на русском языке. "
    "КАТЕГОРИЧЕСКИ запрещены китайские иероглифы и иностранные слова (допустимы только "
    "биржевые тикеры латиницей, например SBER, и аббревиатуры RSI/SMA). Опирайся ТОЛЬКО "
    "на приведённые данные, не выдумывай чисел, не повторяйся, не дублируй вопрос."
)


def _build_narrative_prompt(question: str, grounding: str) -> str:
    # Язык/формат заданы в system-роли (_NARRATIVE_SYSTEM). Здесь — задача и данные.
    return (
        "Дай связный аналитический ответ на вопрос по структуре: текущее состояние → "
        "ключевые драйверы → риски → краткий вывод (4–8 предложений).\n\n"
        f"ДАННЫЕ:\n{grounding}\n\nВОПРОС: {question}"
    )


def _has_cjk(s: str) -> bool:
    """Есть ли в строке CJK/кана/хангыль/CJK-пунктуация/полноширинные формы.

    Полноширинные формы (U+FF00–FFEF: «（），。») и CJK-пунктуация (U+3000–303F) —
    тоже признак языкового срыва Qwen, ловим их наравне с иероглифами.
    """
    return any(0x4E00 <= (o := ord(c)) <= 0x9FFF or 0x3040 <= o <= 0x30FF
              or 0xAC00 <= o <= 0xD7AF or 0x3000 <= o <= 0x303F
              or 0xFF00 <= o <= 0xFFEF for c in s)


def _clean_narrative(text: str) -> str:
    """Чистит ответ LLM от языкового дрейфа, повторов и markdown-артефактов (чистая).

    Qwen 7B(q4) на длинной русской генерации КОЛЕБЛЕТСЯ рус↔кит: даёт русский, срывается в
    китайский, возвращается. Обрезка по первому CJK теряла бы хорошие русские куски дальше,
    поэтому чистим ПОСЕГМЕНТНО: разбиваем на предложения, выкидываем сегменты с CJK и
    смешанные, дедупим повторы (модель повторяет вывод), убираем markdown-заголовки.
    """
    import re

    text = re.sub(r"#+\s*", "", text)                      # markdown-заголовки «### …»
    # Утечка chat-шаблона: модель дописывает фейковые ходы диалога («user Извините за
    # недоразумение. assistant Цена…»). Ролевые маркеры в начале сегмента срезаем,
    # извинения-мета (контент фейкового хода) выкидываем целиком.
    role_re = re.compile(r"^(?:user|assistant|system)\b[\s:,.—-]*", re.IGNORECASE)
    meta_re = re.compile(r"^(?:извин|прошу прощени|sorry)", re.IGNORECASE)
    # Разбиваем по предложениям. Для лат./рус. терминаторов требуем ПРОБЕЛ после (иначе
    # «49.15» рвётся на «49.»+«15»); CJK-терминаторы (。！？) и переводы строк — без пробела
    # (китайская фраза без пробелов иначе склеится с соседним русским в один сегмент).
    parts = re.split(r"(?<=[.!?])\s+|(?<=[。！？])|\n+", text)
    out, seen = [], set()
    for p in parts:
        p = p.strip()
        while role_re.match(p):                            # «user user …» — срезаем все
            p = role_re.sub("", p, count=1).strip()
        if not p or meta_re.match(p) or _has_cjk(p):       # пустые/мета/иноязычные — мимо
            continue
        # латиница допустима (тикеры SMA/RSI/SBER), но не куски с битыми словами «отonestlyх»
        if re.search(r"[a-zA-Z]{3,}[а-яё]|[а-яё][a-zA-Z]{3,}", p):
            continue
        key = re.sub(r"\W+", "", p.lower())[:50]           # дедуп по началу нормализованного
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return " ".join(out).strip().lstrip(".:;— ").strip()


def _digit_runs(text: str) -> set[str]:
    """Числа из текста как последовательности цифр (разделители убраны), длиной ≥ 2.

    Однозначные числа («3 новости») слишком шумны для проверки — пропускаем. «49,15» и
    «16%» дают руны «4915» и «16»."""
    import re

    return {re.sub(r"\D", "", n) for n in re.findall(r"\d[\d.,]*", text)
            if len(re.sub(r"\D", "", n)) >= 2}


def _strip_unsupported_numbers(text: str, grounding: str) -> str:
    """Убирает предложения нарратива, утверждающие число, которого НЕТ в grounding.

    Анти-галлюцинация: LLM-аналитик не должен выдумывать цифры (цены, проценты). Поддержка
    — мягкая: число «поддержано», если его цифровая руна является подстрокой какой-либо
    руны grounding или наоборот (терпит округление 49.15↔49). Предложения без чисел не
    трогаем (качественные суждения). Чистая функция — тестируется."""
    import re

    g_runs = _digit_runs(grounding)
    kept = []
    for sent in re.split(r"(?<=[.!?])\s+", text):
        nums = _digit_runs(sent)
        unsupported = [n for n in nums
                       if not any(n in g or g in n for g in g_runs)]
        if not unsupported:
            kept.append(sent)
    return " ".join(kept).strip()


def _narrate(question: str, grounding: str, *, use_llm: bool) -> tuple[str | None, bool]:
    if use_llm and grounding:
        # repeat_penalty + stop гасят зацикливание/служебный хвост; temperature низкая
        # для фактологичности и против языковых срывов (рус+кит). _clean_narrative —
        # последний рубеж: обрезает китайский дрейф в конце длинной генерации.
        text = _router_llm(
            _build_narrative_prompt(question, grounding),
            system=_NARRATIVE_SYSTEM,
            repeat_penalty=1.3,
            temperature=0.3,
            stop=["\nВОПРОС:", "\nДАННЫЕ:", "\nОтвет:", "\nОтвет :"],
        )
        if text:
            cleaned = _clean_narrative(text)
            # Анти-галлюцинация: режем предложения с числами, которых нет в данных.
            grounded = _strip_unsupported_numbers(cleaned, grounding)
            if grounded:
                return grounded, True
    return None, False


_OWNER_PF_TTL = 60.0                               # сек: окно кэша отчёта портфеля для ask
_owner_pf_cache: dict = {}                         # user_id|None → {"ts": float, "report": ...}


def owner_portfolio_report(user_id: int | None = None):
    """Отчёт по портфелю пользователя (`user_id=None` — владелец) с коротким TTL-кэшем (Волна 6):
    NL-вопросы про портфель/актив не пересчитывают тяжёлый `live_portfolio_report` на каждый
    запрос. КЭШ КЛЮЧУЕТСЯ ПО user_id — бот-юзер видит СВОЙ портфель, а не владельца. None — сбой/
    нет данных (ask продолжает без портфеля). buy/sell видны с задержкой ≤TTL."""
    import time

    now = time.monotonic()
    cached = _owner_pf_cache.get(user_id)
    if cached is not None and now - cached["ts"] < _OWNER_PF_TTL:
        return cached["report"]
    try:
        from geoanalytics.analytics.portfolio import live_portfolio_report
        from geoanalytics.storage.db import session_scope

        with session_scope() as session:
            rep = live_portfolio_report(session, user_id=user_id)
    except Exception as exc:
        log.warning("ask_owner_portfolio_failed", error=str(exc))
        return None
    _owner_pf_cache[user_id] = {"ts": now, "report": rep}
    return rep


# --- Трек B: скринер идей «что купить/докупить» (recommend/help) --------------- #
_SCREEN_TTL = 300.0                                # сек: TTL-кэш идей (скрин вселенной не дёшев)
_screen_cache: dict = {}                           # (user_id, mode) → {"ts", "ideas"}
_HELP_MENU = (
    "Помогу разобраться в рынке простыми словами. Вот с чем могу помочь — спрашивайте обычными "
    "словами:\n"
    "• «что мне купить?» / «куда вложить?» — подберу идеи по активам\n"
    "• «что докупить?» — идеи к вашему портфелю\n"
    "• «разбери SBER» — анализ конкретной акции (тренд, риск, стойка)\n"
    "• «что на рынке?» — сводка и настроение рынка\n"
    "• «мой портфель» — риск, состав, экспозиция"
)


def _screen_mode(question: str) -> str:
    """Режим скринера из вопроса: докупить к портфелю / новое / авто."""
    q = question.lower()
    if any(k in q for k in ("докуп", "добав", "доложить", "к портфел", "уже есть")):
        return "topup"
    if any(k in q for k in ("новое", "новые", "с нуля", "не держу", "ещё не", "впервые")):
        return "new"
    return "auto"


def _screen_ideas(user_id: int | None, mode: str) -> list[dict]:
    """Идеи скринера (dict-ы) с TTL-кэшем по (user_id, mode). [] при сбое/нет данных."""
    import time

    key = (user_id, mode)
    now = time.monotonic()
    cached = _screen_cache.get(key)
    if cached is not None and now - cached["ts"] < _SCREEN_TTL:
        return cached["ideas"]
    try:
        from geoanalytics.analytics.screen import screen_universe
        from geoanalytics.storage.db import session_scope

        with session_scope() as session:
            ideas = [i.to_dict() for i in
                     screen_universe(session, user_id=user_id, mode=mode, limit=5)]
    except Exception as exc:  # noqa: BLE001 — скрин не валит ask
        log.warning("ask_screen_failed", error=str(exc))
        return []
    _screen_cache[key] = {"ts": now, "ideas": ideas}
    return ideas


_DISCLAIMER = "\n\n⚠️ Образовательная аналитика, не индивидуальная инвестиционная рекомендация."


def _answer_recommend(question: str, intent: str, user_id: int | None) -> AskResult:
    """Трек B: ответ на «что купить/докупить» (recommend) и непонятный вопрос (help).

    Шаблонный (без LLM) — работает и при занятом замке генерации/выключенном Ollama: ценность
    несёт структурный список идей (`recommendations`), который рендерят бот и веб. help добавляет
    дружелюбное меню + пару идей, recommend — вводную под режим. Всегда с дисклеймером.
    """
    mode = _screen_mode(question)
    ideas = _screen_ideas(user_id, mode)
    if intent == "help":
        answer = _HELP_MENU
        if ideas:
            answer += "\n\nА вот пара идей прямо сейчас:\n" + "\n".join(
                f"• {i['ticker']} ({i['name']}) — {i['label']}" for i in ideas[:3])
    elif not ideas:
        answer = ("Сейчас по данным нет уверенных идей для покупки — рынок без явных сигналов. "
                  "Загляните позже или спросите про конкретный актив.")
    else:
        answer = {"topup": "Идеи, что можно докупить к вашему портфелю:",
                  "new": "Идеи для новых покупок (вне вашего портфеля):",
                  "auto": "Несколько идей под ваш вопрос:"}[mode]
    facts = [f"{i['ticker']} — {i['label']} ({i['action']})" for i in ideas]
    log.info("ask", intent=intent, used_llm=False, ideas=len(ideas), mode=mode)
    return AskResult(
        question=question, intent=intent, answer=answer + _DISCLAIMER, facts=facts,
        recommendations=ideas, used_llm=False,
        note="Подбор по факторам (теханализ + настроение + прогнозы брокеров).",
    )


def _portfolio_recommendation(weight: float, rc: float | None) -> str:
    """Грубое правило-рекомендация относительно портфеля (без LLM, грунтовано числами):
    высокая концентрация по доле / непропорциональный вклад в риск / в норме."""
    if weight >= 25.0:
        return (f"высокая концентрация — {weight:.0f}% портфеля; рассмотрите снижение доли "
                "для диверсификации")
    if rc is not None and weight > 0 and rc >= 1.5 * weight:
        return (f"вносит в риск больше, чем весит ({rc:.0f}% риска при доле {weight:.0f}%) — "
                "источник волатильности портфеля")
    return f"доля {weight:.0f}% — в пределах диверсификации"


def _holding_correlations(rep, ticker: str) -> list[dict]:
    """Сильнейшие корреляции тикера с остальными холдингами (топ-2 по |r|) — для оценки
    диверсификации/концентрации позиции относительно портфеля."""
    cors = []
    for (a, b), r in rep.correlations.items():
        if a == ticker:
            cors.append((b, r))
        elif b == ticker:
            cors.append((a, r))
    cors.sort(key=lambda x: abs(x[1]), reverse=True)
    return [{"ticker": t, "r": round(r, 2)} for t, r in cors[:2]]


def _portfolio_block(ticker: str | None, user_id: int | None = None) -> dict | None:
    """Контекст актива в портфеле ПОЛЬЗОВАТЕЛЯ (Волна 6): доля/β/вклад в риск/корреляции +
    рекомендация. Берёт кэшированный отчёт по `user_id` (None — владелец). Не в портфеле /
    нет цен → None."""
    if not ticker:
        return None
    rep = owner_portfolio_report(user_id)
    if rep is None or rep.error:
        return None
    pos = next((p for p in rep.positions if p.ticker == ticker), None)
    if pos is None or pos.weight_pct is None:
        return None
    # Вклад позиции в VaR портфеля в ₽: доля её вклада в дисперсию × портфельный VaR95.
    var_rub = None
    if rep.var95_1d_rub is not None and pos.risk_contribution_pct is not None:
        var_rub = round(rep.var95_1d_rub * pos.risk_contribution_pct / 100.0)
    return {
        "ticker": ticker,
        "weight_pct": pos.weight_pct,
        "beta_market": pos.betas.get("market"),
        "risk_contribution_pct": pos.risk_contribution_pct,
        "var_contribution_rub": var_rub,
        "correlations": _holding_correlations(rep, ticker),
        "recommendation": _portfolio_recommendation(pos.weight_pct, pos.risk_contribution_pct),
    }


def answer(question: str, *, use_llm: bool = True, user_id: int | None = None) -> AskResult:
    """Главная точка входа: вопрос → AskResult (интент, факты, нарратив, цитаты).

    `user_id` (None — владелец) определяет, ЧЕЙ портфель подмешивается в портфельный блок и
    интент `portfolio` — бот-юзер видит СВОЙ портфель, дашборд/CLI — владельца.

    LLM-генерация защищена МЕЖПРОЦЕССНЫМ замком (бот↔дашборд, [[core.locks]]): если генерация уже
    идёт в другом запросе/процессе — не запускаем вторую (OOM на слабом железе), а ДЕГРАДИРУЕМ на
    ответ без ИИ с честной пометкой. Так несколько человек не генерируют одновременно.
    """
    question = (question or "").strip()
    if not question:
        return AskResult(question="", intent="unknown",
                         answer="Задайте вопрос об активе, рынке, событиях или новостях.",
                         note="empty")
    if not use_llm:
        return _answer_impl(question, use_llm=False, user_id=user_id)
    try:
        with llm_generation_lock():
            return _answer_impl(question, use_llm=True, user_id=user_id)
    except LLMBusy:
        res = _answer_impl(question, use_llm=False, user_id=user_id)
        res.note = ("⏳ Система занята генерацией другого ответа — собрал без ИИ. "
                    "Повторите через минуту для полного разбора.")
        return res


def _answer_impl(question: str, *, use_llm: bool, user_id: int | None) -> AskResult:
    """Сборка ответа (под замком генерации, если use_llm). Вопрос уже очищен и непуст."""
    intent_obj, llm_intent = resolve_intent(question, use_llm=use_llm)
    # Трек B: recommend/help — отдельный путь (скринер идей, шаблонный ответ без LLM-нарратива).
    if intent_obj["intent"] in ("recommend", "help"):
        res = _answer_recommend(question, intent_obj["intent"], user_id)
        if not llm_intent:
            res.note = (res.note + " Ответ собран без LLM (эвристика).").strip()
        return res
    facts, citations, grounding = _dispatch(intent_obj, question, user_id)
    narrative, llm_narr = _narrate(question, grounding, use_llm=use_llm)

    # Волна 6 (а): актив запроса в портфеле пользователя → выделенный портфельный блок (доля/β/
    # риск/корреляции/рекомендация). Числа точные (из отчёта), рендерится отдельно от нарратива.
    ticker = intent_obj.get("ticker")
    pf = _portfolio_block(ticker, user_id)
    rag_trace = _rag_trace(citations)

    answer_text = narrative or ("\n".join(facts) if facts else "Данных по запросу нет.")
    note = "" if (llm_intent or llm_narr) else "Ответ собран без LLM (эвристика/шаблон)."
    log.info("ask", intent=intent_obj["intent"], used_llm=llm_intent or llm_narr,
             facts=len(facts), citations=len(citations), portfolio=pf is not None,
             trace=len(rag_trace))
    return AskResult(
        question=question, intent=intent_obj["intent"], answer=answer_text,
        facts=facts, citations=citations, ticker=ticker,
        used_llm=llm_intent or llm_narr, note=note, portfolio=pf, rag_trace=rag_trace,
    )
