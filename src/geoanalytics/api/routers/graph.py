"""HTMX/Jinja router for impact graphs, market tree, and heatmap."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from geoanalytics.api import web
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import Asset

router = APIRouter()

# Факторы влияния (C): корреляция доходности с сырьём/валютами (correlate_asset).
_FACTOR_CSS = {"brent": "gn-commodity", "gold": "gn-commodity", "silver": "gn-commodity",
               "platinum": "gn-commodity", "palladium": "gn-commodity",
               "usd_rub": "gn-fx", "usd_eur": "gn-fx"}
_FACTOR_RU = {"brent": "Brent", "gold": "золото", "silver": "серебро",
              "platinum": "платина", "palladium": "палладий",
              "usd_rub": "USD/RUB", "usd_eur": "USD/EUR"}
_EVENT_AGG = {"positive": ("up", "Позитивные события", "↑"),
              "negative": ("down", "Негативные события", "↓"),
              "neutral": ("muted", "Нейтральные события", "•")}


def _graph_context(ticker: str) -> dict:
    """Граф влияния актива: радиальное ДЕРЕВО «актив → агрегаты → листья».

    Группировка (A) вместо плоской звезды: ветвь СЕКТОР (gn-sector) тянет к себе пиров
    (gn-peer, кликабельны → свой граф); события агрегированы ПО НАПРАВЛЕНИЮ — ветви
    «↑ позитив» / «↓ негатив» / «• нейтрально», под каждой отдельные события (размер+подпись =
    magnitude, заголовок в подсказке). Факторы влияния не только тикеры (C): сырьё/валюты
    как ветви-листья с весом = |корреляция| доходности (gn-commodity/gn-fx), плюс макро-
    драйверы сектора (gn-macro, без числа). События очищены top_impacts (живые связи).
    """
    from geoanalytics.analytics.correlations import correlate_asset
    from geoanalytics.analytics.graph_weight import (
        MACRO_WEIGHT,
        aggregate_weight,
        asset_node_weight,
    )
    from geoanalytics.api.charts import radial_tree, relax_overlaps
    from geoanalytics.context.events import top_impacts_for_asset
    from geoanalytics.context.graph import factors_for_asset, sector_macro_factors

    t = ticker.upper().strip()
    graph = None
    with session_scope() as session:
        asset = session.scalars(select(Asset).where(Asset.ticker == t)).first() if t else None
        if asset is not None:
            branches: list[dict] = []
            factors = factors_for_asset(session, asset)
            # Сектор → пиры (внешними листьями ветви). A2: размер пира = его собственный вес
            # (давление+сентимент+TA), а не константа; сектор = агрегат весов пиров.
            peer_tickers = factors.peers[:8]
            if factors.sector and peer_tickers:
                peers = list(session.scalars(
                    select(Asset).where(Asset.ticker.in_(peer_tickers))))
                children = [{"label": a.ticker, "css": "gn-peer",
                             "size": asset_node_weight(session, a.id, with_ta=True),
                             "title": f"Пир по сектору: {a.name or a.ticker}",
                             "url": f"/ui/graph?ticker={a.ticker}"} for a in peers]
                branches.append({"label": factors.sector, "css": "gn-sector",
                                 "size": aggregate_weight([c["size"] for c in children]),
                                 "title": f"Сектор: {factors.sector}", "children": children})
            elif factors.sector:
                branches.append({"label": factors.sector, "css": "gn-sector", "size": 0.55,
                                 "title": f"Сектор: {factors.sector}", "url": None})
            # События → агрегаты по направлению (A: один зелёный/красный узел)
            groups: dict[str, list[dict]] = {"positive": [], "negative": [], "neutral": []}
            for e in top_impacts_for_asset(session, asset.id, hours=720, limit=20):
                groups.setdefault(e["direction"], groups["neutral"]).append(e)
            # Размер агрегата = СУММАРНАЯ сила группы (Σ|magnitude|), нормированная на самую
            # «тяжёлую» группу: доминирующее направление — самый крупный узел (передаёт, НАСКОЛЬКО
            # сильно влияют события, а не их среднее). Подпись несёт точное число: «↓14 · Σ7.3».
            totals = {d: sum(float(e["magnitude"] or 0.0) for e in evs)
                      for d, evs in groups.items()}
            max_total = max(totals.values()) or 1.0
            for dirn, evs in groups.items():
                if not evs:
                    continue
                css, word, arrow = _EVENT_AGG[dirn]
                children = [{
                    "label": f"{e['magnitude']:.2f}",
                    "title": (f"[{e['type']}] " if e.get("type") else "") + (e["title"] or ""),
                    "css": css, "size": min(max(float(e["magnitude"] or 0.0), 0.0), 1.0),
                    "url": e.get("url")} for e in evs]
                total = totals[dirn]
                branches.append({"label": f"{arrow}{len(evs)} · Σ{total:.1f}", "css": css,
                                 "size": min(total / max_total, 1.0),
                                 "title": f"{word}: {len(evs)} соб., Σсила {total:.2f}",
                                 "children": children})
            # Факторы (C) и макро — отдельными АГРЕГАТАМИ (как события ↑/↓), а не одиночными
            # ветвями у центра: их листья уходят на внешнее кольцо с подписями НАРУЖУ, иначе
            # 9 факторов/макро лепятся в один сектор у центра и подписи наслаиваются.
            cors = correlate_asset(session, asset)
            ranked = sorted(((k, v) for k, v in cors.items() if k in _FACTOR_CSS),
                            key=lambda kv: abs(kv[1]), reverse=True)[:6]
            if ranked:
                fchildren = [{"label": f"{_FACTOR_RU[k]} {r:+.2f}", "css": _FACTOR_CSS[k],
                              "size": min(abs(r), 1.0), "url": None,
                              "title": f"Корреляция доходности с {_FACTOR_RU[k]}: {r:+.2f}"}
                             for k, r in ranked]
                branches.append({"label": "Факторы", "css": "gn-commodity",
                                 "size": min(abs(ranked[0][1]), 1.0),
                                 "title": "Сырьё и валюты (вес = |корреляция| доходности)",
                                 "children": fchildren})
            macro = sector_macro_factors(factors.sector)[:3]
            if macro:
                mchildren = [{"label": mf, "css": "gn-macro", "size": MACRO_WEIGHT, "url": None,
                              "title": f"Макро-драйвер сектора: {mf}"} for mf in macro]
                branches.append({"label": "Макро", "css": "gn-macro",
                                 "size": min(MACRO_WEIGHT + 0.1, 1.0),
                                 "title": "Макро-драйверы сектора", "children": mchildren})
            graph = relax_overlaps(radial_tree(t, branches))
    return {"ticker": t, "graph": graph, "assets": web.list_assets()}


def _market_graph_context() -> dict:
    """Большой граф рынка (B): дерево ИНДЕКС → секторы → топ-активы → важные события.

    4 уровня: корень IMOEX → сектора («сферы») → по топ-активов сектора (ранг = новостное
    давление news_pressure за 7д) → по топ-событий актива (top_impacts, размер=magnitude).
    Узлы крупнее у тех, у кого выше важность. Раскладка `radial_layout` (произвольная глубина,
    тот же контракт, что граф тикера → шаблон `_graph_svg.html` общий). Тяжеловато (десятки
    активов), потому отдельная страница с автообновлением раз в 60с.
    """
    from geoanalytics.analytics.graph_weight import (
        asset_node_weight,
        normalize_weight,
        recent_turnover,
    )
    from geoanalytics.analytics.pressure import news_pressure
    from geoanalytics.api.charts import radial_layout, relax_overlaps
    from geoanalytics.context.events import top_impacts_for_asset
    from geoanalytics.context.graph import assets_in_sector
    from geoanalytics.storage.models import Sector

    _DIR_CSS = {"positive": "up", "negative": "down", "neutral": "muted"}
    graph = None
    with session_scope() as session:
        sectors = session.scalars(select(Sector).order_by(Sector.name)).all()
        sector_assets = {sec: assets_in_sector(session, sec.id) for sec in sectors}
        # Вес сектора на индексе = Σ оборота его активов (близко к рыночной доле). Считаем
        # обороты по всем активам разом, затем нормируем сектора к самому «тяжёлому».
        turnover = recent_turnover(
            session, [a.id for assets in sector_assets.values() for a in assets])
        sec_turn = {sec: sum(turnover.get(a.id, 0.0) for a in assets)
                    for sec, assets in sector_assets.items()}
        peak_turn = max(sec_turn.values(), default=0.0)
        sector_branches: list[dict] = []
        for sec in sectors:
            ranked = sorted(
                ((a, news_pressure(session, a.id, window=7)) for a in sector_assets[sec]),
                key=lambda av: av[1], reverse=True)
            asset_nodes: list[dict] = []
            for a, pressure in ranked[:6]:        # топ-6 активов сектора по давлению
                ev_nodes = [{
                    "label": f"{e['magnitude']:.2f}",
                    "title": (f"[{e['type']}] " if e.get("type") else "") + (e["title"] or ""),
                    "css": _DIR_CSS.get(e["direction"], "muted"),
                    "size": min(max(float(e["magnitude"] or 0.0), 0.0), 1.0),
                    "url": e.get("url"),
                    # Прорежаем кольцо событий: только значимые (magnitude ≥ 0.15), до 3 на актив,
                    # иначе внешнее кольцо переполнено и точки налезают.
                } for e in top_impacts_for_asset(session, a.id, hours=720, limit=3)
                    if float(e["magnitude"] or 0.0) >= 0.15]
                # A2: размер актива = давление+сентимент (TA пропускаем — десятки активов на
                # автообновлении дорого); давление переиспользуем из ранжирования.
                w = asset_node_weight(session, a.id, pressure=pressure)
                asset_nodes.append({
                    "label": a.ticker, "css": "gn-peer",
                    "size": w, "url": f"/ui/graph?ticker={a.ticker}",
                    "title": f"{a.name} · давление {pressure:.2f}", "children": ev_nodes})
            if asset_nodes:
                sector_branches.append({
                    "label": sec.name, "css": "gn-sector",
                    # Размер сектора = его вес на индексе (оборот), а не агрегат новостей.
                    "size": normalize_weight(sec_turn[sec], peak_turn),
                    "title": f"Сектор: {sec.name} · вес на индексе по обороту",
                    "children": asset_nodes})
        if sector_branches:
            root = {"label": "IMOEX", "children": sector_branches}
            # Крупнее холст → больше окружность внешнего кольца при тех же радиусах узлов →
            # меньше слипания (плюс анизотропный relax добивает остаток).
            graph = relax_overlaps(radial_layout(root, width=1600, height=1600, pad=190))
    return {"graph": graph, "is_market": True}


def _market_heatmap_context() -> dict:
    """Карта рынка под графом индекса (Finviz-стиль): секторы→активы, площадь ∝ оборот, цвет ∝ Δ%.

    Размер плитки — последний дневной оборот (close·volume, «текущий» объём торгов), цвет —
    дневное изменение цены (зелёный рост / красный падение). Группировка по секторам с подписью.
    Тяжеловато (обороты+изменения по всем активам), поэтому TTL-кэш и автообновление 60с.
    """
    def _build() -> dict:
        from geoanalytics.analytics.graph_weight import turnover_and_change
        from geoanalytics.analytics.prices import latest_live_market
        from geoanalytics.api.charts import market_heatmap
        from geoanalytics.context.graph import assets_in_sector
        from geoanalytics.storage.models import Sector

        with session_scope() as session:
            sectors = session.scalars(select(Sector).order_by(Sector.name)).all()
            sec_assets = {sec: assets_in_sector(session, sec.id) for sec in sectors}
            all_assets = [a for assets in sec_assets.values() for a in assets]
            # Сегодняшние данные — из живого среза MOEX (VALTODAY-оборот + изменение к закрытию);
            # для бумаг вне live-фида (фонды/неликвид) — фолбэк на последнюю дневную свечу.
            live = latest_live_market(session, [a.ticker for a in all_assets])
            eod = turnover_and_change(session, [a.id for a in all_assets])
            groups = []
            for sec in sectors:
                items = []
                for a in sec_assets[sec]:
                    lv = live.get(a.ticker)
                    if lv and lv[0]:                      # есть сегодняшний оборот
                        turnover, pct = lv
                    elif a.id in eod and eod[a.id][0] > 0:  # иначе вчерашняя свеча
                        turnover, pct = eod[a.id]
                    else:
                        continue
                    items.append({"label": a.ticker, "value": turnover, "pct": pct})
                if items:
                    groups.append({"label": sec.name, "items": items})
        return {"heatmap": market_heatmap(groups)}

    return web._cached("market_heatmap", _build)


@router.get("/ui/graph", response_class=HTMLResponse)
def graph_page(request: Request, ticker: str = "SBER"):
    """Граф влияния: отдельная страница — дерево «актив → сектор/события/факторы»."""
    return web.templates.TemplateResponse(request, "graph.html", web._graph_context(ticker))


@router.get("/ui/partials/graph", response_class=HTMLResponse)
def graph_partial(request: Request, ticker: str = "SBER"):
    """HTMX-фрагмент графа (D): автообновление точек влияния по hx-trigger every 60s."""
    return web.templates.TemplateResponse(request, "_graph_svg.html", web._graph_context(ticker))


@router.get("/ui/graph/market", response_class=HTMLResponse)
def market_graph_page(request: Request):
    """Большой граф рынка (B): дерево IMOEX → секторы → активы → события."""
    return web.templates.TemplateResponse(request, "graph_market.html", web._market_graph_context())


@router.get("/ui/partials/graph/market", response_class=HTMLResponse)
def market_graph_partial(request: Request):
    """HTMX-фрагмент большого графа (D): автообновление раз в 60с."""
    return web.templates.TemplateResponse(request, "_graph_svg.html", web._market_graph_context())


@router.get("/ui/partials/graph/heatmap", response_class=HTMLResponse)
def market_heatmap_partial(request: Request):
    """HTMX-фрагмент карты рынка (объём/изменение по секторам): автообновление раз в 60с."""
    return web.templates.TemplateResponse(request, "_market_heatmap.html", web._market_heatmap_context())
