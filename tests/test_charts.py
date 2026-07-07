"""Тесты графиков и агрегации свечей (M6): чистые функции, без БД."""

from __future__ import annotations

from datetime import datetime

from geoanalytics.analytics.backtest import run
from geoanalytics.analytics.resample import resample_ohlc, resample_ohlcv
from geoanalytics.api.charts import (
    _event_markers,
    candles,
    date_labels,
    equity_chart,
    pie,
    radial_graph,
    radial_layout,
    radial_tree,
    rsi_panel,
    sentiment_strip,
    sparkline,
    volume_bars,
)


def _row(y, m, d, o, h, low, c):
    return (datetime(y, m, d), float(o), float(h), float(low), float(c))


# --- date_labels ---
def test_date_labels_count_and_bounds():
    ts = [datetime(2026, 1, i + 1) for i in range(20)]
    labels = date_labels(ts, width=600, pad=0, count=5)
    assert 2 <= len(labels) <= 6
    assert labels[0]["x"] == 0.0          # первая подпись слева
    assert labels[-1]["x"] == 600.0       # последняя справа
    assert labels[0]["text"] == "01.01.26"


def test_date_labels_empty_and_single():
    assert date_labels([]) == []
    one = date_labels([datetime(2026, 6, 1)], width=100, pad=0)
    assert len(one) == 1 and one[0]["x"] == 50.0


# --- sparkline labels passthrough ---
def test_sparkline_carries_labels():
    sp = sparkline([1, 2, 3], labels=[{"x": 0.0, "text": "x"}])
    assert sp["labels"] == [{"x": 0.0, "text": "x"}]


# --- xhair (перекрестие-курсор) ---
def test_sparkline_xhair_bars_and_axis():
    """xhair несёт ось Y (lo/hi/pad/w/h) и по бару на точку; даты подписывают бары."""
    ds = [datetime(2026, 1, 1), datetime(2026, 1, 2), datetime(2026, 1, 3)]
    sp = sparkline([10, 20, 15], width=200, height=100, pad=5, dates=ds)
    xh = sp["xhair"]
    assert xh["kind"] == "line" and xh["w"] == 200 and xh["h"] == 100 and xh["pad"] == 5
    assert xh["lo"] <= 10 and xh["hi"] >= 20
    assert len(xh["bars"]) == 3
    b = xh["bars"][1]
    assert b["v"] == 20 and b["t"] == "02.01.26"
    # y координата точки совпадает с y в полилинии (для дота на линии)
    assert "x" in b and "y" in b


def test_sparkline_xhair_without_dates_blank_label():
    sp = sparkline([1, 2, 3])
    assert all(b["t"] == "" for b in sp["xhair"]["bars"])


def test_candles_xhair_carries_ohlc():
    """xhair свечей несёт O/H/L/C, дату и направление по каждому бару."""
    rows = [_row(2026, 1, 1, 100, 110, 95, 105), _row(2026, 1, 2, 105, 108, 100, 101)]
    ch = candles(rows)
    xh = ch["xhair"]
    assert xh["kind"] == "candles" and len(xh["bars"]) == 2
    b0 = xh["bars"][0]
    assert (b0["o"], b0["h"], b0["l"], b0["c"]) == (100, 110, 95, 105)
    assert b0["up"] is True and b0["t"] == "01.01.26"
    assert xh["bars"][1]["up"] is False


# --- overlays (C1) ---
def test_sparkline_overlay_mapped_and_warmup_skipped():
    """Оверлей маппится в полилинию; None-точки прогрева пропускаются."""
    ov = [{"name": "SMA", "values": [None, 10.5, 11.5, 12.5], "css": "#abc", "dash": ""}]
    sp = sparkline([10, 11, 12, 13], width=100, height=100, pad=0, overlays=ov)
    assert len(sp["overlays"]) == 1
    o = sp["overlays"][0]
    assert o["name"] == "SMA" and o["css"] == "#abc"
    assert len(o["points"].split(" ")) == 3      # первая точка (None) пропущена


def test_overlay_extends_axis_but_not_price_label():
    """Оверлей выше цены расширяет координатный диапазон, но подпись min/max = цена."""
    ov = [{"name": "B", "values": [20, 20, 20], "css": "#000"}]
    sp = sparkline([10, 11, 12], overlays=ov)
    assert sp["min"] == 10 and sp["max"] == 12   # подпись — диапазон цены, не оверлея
    assert len(sp["overlays"]) == 1


def test_candles_carry_overlays():
    rows = [_row(2026, 1, 1, 10, 12, 9, 11), _row(2026, 1, 2, 11, 13, 10, 12)]
    ch = candles(rows, overlays=[{"name": "SMA", "values": [10.5, 11.5], "css": "#abc"}])
    assert len(ch["overlays"]) == 1 and ch["overlays"][0]["name"] == "SMA"


def test_overlay_all_none_dropped():
    """Ряд целиком из None (полный прогрев) отбрасывается."""
    sp = sparkline([1, 2, 3], overlays=[{"name": "x", "values": [None, None, None]}])
    assert sp["overlays"] == []


def test_chart_without_overlays_has_empty_list():
    assert sparkline([1, 2, 3])["overlays"] == []
    assert candles([_row(2026, 1, 1, 1, 1, 1, 1)])["overlays"] == []


# --- candles ---
def test_candles_geometry_and_direction():
    rows = [_row(2026, 1, 1, 10, 12, 9, 11),    # up (close>open)
            _row(2026, 1, 2, 11, 11.5, 8, 9)]   # down
    ch = candles(rows, width=100, height=100, pad=0)
    assert ch["n"] == 2
    assert ch["candles"][0]["up"] is True
    assert ch["candles"][1]["up"] is False
    # глобальный min/max берутся из low/high всего ряда
    assert ch["min"] == 8.0 and ch["max"] == 12.0
    # фитиль выше тела (меньшая y — выше в SVG)
    c0 = ch["candles"][0]
    assert c0["wick_top"] <= c0["y"]


def test_candles_empty():
    assert candles([]) is None


# --- resample_ohlc ---
def test_resample_weekly_aggregates_ohlc():
    # пн-ср одной недели → одна свеча: open первой, close последней, high/low экстремумы
    rows = [_row(2026, 6, 1, 10, 12, 9, 11),
            _row(2026, 6, 2, 11, 15, 10, 14),
            _row(2026, 6, 3, 14, 14, 7, 8)]
    out = resample_ohlc(rows, "W")
    assert len(out) == 1
    _ts, o, h, low, c = out[0]
    assert (o, h, low, c) == (10.0, 15.0, 7.0, 8.0)


def test_resample_monthly_splits_by_month():
    rows = [_row(2026, 1, 15, 1, 2, 1, 2), _row(2026, 2, 3, 2, 3, 2, 3)]
    out = resample_ohlc(rows, "M")
    assert len(out) == 2


def test_resample_unknown_period_passthrough():
    rows = [_row(2026, 1, 1, 1, 1, 1, 1)]
    assert resample_ohlc(rows, "D") == rows
    assert resample_ohlc([], "W") == []


# --- resample_ohlcv (объём суммируется) ---
def _rowv(y, m, d, o, h, low, c, v):
    return (datetime(y, m, d), float(o), float(h), float(low), float(c), v)


def test_resample_ohlcv_sums_volume():
    rows = [_rowv(2026, 6, 1, 10, 12, 9, 11, 100.0),
            _rowv(2026, 6, 2, 11, 15, 10, 14, 200.0),
            _rowv(2026, 6, 3, 14, 14, 7, 8, 50.0)]
    out = resample_ohlcv(rows, "W")
    assert len(out) == 1
    _ts, o, h, low, c, v = out[0]
    assert (o, h, low, c) == (10.0, 15.0, 7.0, 8.0)
    assert v == 350.0                              # объём суммирован


def test_resample_ohlcv_none_volume_safe():
    rows = [_rowv(2026, 6, 1, 10, 12, 9, 11, None),
            _rowv(2026, 6, 2, 11, 15, 10, 14, None)]
    assert resample_ohlcv(rows, "W")[0][5] is None  # все None → None


# --- volume_bars (C2) ---
def test_volume_bars_geometry_and_color():
    vb = volume_bars([100.0, None, 200.0], [True, False, False],
                     width=100, height=40, pad=0)
    assert vb is not None and vb["max"] == 200.0
    assert len(vb["bars"]) == 2                    # None-объём пропущен
    assert vb["bars"][0]["up"] is True
    # больший объём → выше столбик (меньшая y в SVG)
    assert vb["bars"][1]["y"] <= vb["bars"][0]["y"]


def test_volume_bars_empty():
    assert volume_bars([None, None], [True, True]) is None
    assert volume_bars([0.0, 0.0], [True, True]) is None


def test_market_heatmap_groups_sizes_and_colors():
    from geoanalytics.api.charts import market_heatmap

    sectors = [
        {"label": "Банки", "items": [
            {"label": "SBER", "value": 1000.0, "pct": 1.5},
            {"label": "VTBR", "value": 200.0, "pct": -0.8}]},
        {"label": "Нефть", "items": [
            {"label": "LKOH", "value": 600.0, "pct": 0.0}]},
    ]
    hm = market_heatmap(sectors, width=800, height=400)
    assert hm is not None
    cells = {c["label"]: c for c in hm["cells"]}
    assert set(cells) == {"SBER", "VTBR", "LKOH"}
    # Площадь ∝ объёму: SBER (1000) крупнее VTBR (200).
    assert cells["SBER"]["w"] * cells["SBER"]["h"] > cells["VTBR"]["w"] * cells["VTBR"]["h"]
    # Цвет по изменению: рост → up, падение → down, ~0 → flat.
    assert cells["SBER"]["css"] == "up" and cells["VTBR"]["css"] == "down"
    assert cells["LKOH"]["css"] == "flat"
    # Сильнее движение → насыщеннее (выше opacity).
    assert cells["SBER"]["opacity"] > cells["VTBR"]["opacity"]
    # Подписи секторов присутствуют.
    assert {s["label"] for s in hm["sectors"]} == {"Банки", "Нефть"}


def test_market_heatmap_empty():
    from geoanalytics.api.charts import market_heatmap

    assert market_heatmap([]) is None
    assert market_heatmap([{"label": "X", "items": []}]) is None
    assert market_heatmap([{"label": "X", "items": [{"label": "A", "value": 0, "pct": 1}]}]) is None


def test_volume_bars_robust_to_single_spike():
    """Один гигантский всплеск не должен расплющивать обычные столбики в пунктир у нуля."""
    vols = [100.0] * 20 + [10000.0]            # 20 обычных дней + один всплеск ×100
    ups = [True] * 21
    vb = volume_bars(vols, ups, width=420, height=60, pad=6)
    inner_h = 60 - 12
    normal = vb["bars"][0]["h"]
    spike = vb["bars"][-1]["h"]
    # Всплеск клипуется на полную высоту, а обычные бары — заметная доля высоты (не пиксель).
    assert spike >= inner_h - 0.5
    assert normal >= inner_h * 0.6
    assert vb["bars"][-1]["clip"] is True and vb["bars"][0]["clip"] is False
    assert vb["max"] == 10000.0                # подпись максимума — реальный максимум


# --- rsi_panel (C3) ---
def test_rsi_panel_line_and_guides():
    closes = [44, 44.3, 44.1, 44.5, 43.9, 44.6, 44.8, 45.1, 45.0, 45.3,
              45.6, 45.4, 45.8, 46.0, 46.2, 46.1, 46.3]
    panel = rsi_panel(closes, width=100, height=100, pad=0, window=14, low=30, high=70)
    assert panel is not None
    assert panel["points"]                          # есть точки RSI
    assert panel["last"] is not None and 0 <= panel["last"] <= 100
    # уровень 70 выше уровня 30 в SVG (меньшая y)
    assert panel["high_y"] < panel["low_y"]


def test_rsi_panel_insufficient():
    assert rsi_panel([1, 2, 3], window=14) is None


# --- equity_chart (C4) ---
def test_equity_chart_markers_and_fill():
    # held=[0,1,1,0,0,1] → 2 сделки; equity/trades берём из реального run.
    closes = [10, 11, 12, 11, 12, 13]
    res = run(closes, [1, 1, 0, 0, 1, 1])
    ec = equity_chart(res.equity_curve, res.trades, width=100, height=100, pad=0)
    assert ec is not None
    assert ec["points"] and ec["dd_fill"]                 # линия и полигон просадки есть
    kinds = [m["kind"] for m in ec["markers"]]
    assert kinds.count("buy") == res.num_trades and kinds.count("sell") == res.num_trades
    assert ec["last"] == round(res.equity_curve[-1], 4)


def test_equity_chart_insufficient():
    assert equity_chart([1.0], []) is None
    assert equity_chart([], []) is None


# --- sentiment_strip (C5) ---
def test_sentiment_strip_colors_by_sign():
    cells = [{"label": "01.06", "score": 0.6}, {"label": "02.06", "score": -0.4},
             {"label": "03.06", "score": 0.0}]
    strip = sentiment_strip(cells, width=300)
    assert strip is not None and strip["n"] == 3
    assert [c["cls"] for c in strip["cells"]] == ["up", "down", "flat"]
    assert strip["cells"][0]["opacity"] > strip["cells"][1]["opacity"]  # |0.6| > |0.4|
    assert strip["first"] == "01.06" and strip["last"] == "03.06"


def test_sentiment_strip_empty():
    assert sentiment_strip([]) is None


# --- event markers (#5) ---
def test_event_markers_color_size_and_tooltip():
    markers = [
        {"idx": 0, "direction": "positive", "magnitude": 1.0, "title": "Отчёт",
         "type": "earnings"},
        {"idx": 2, "direction": "negative", "magnitude": 0.0, "title": "Санкции",
         "type": "sanctions"},
        {"idx": 1, "direction": "neutral", "magnitude": 0.5, "title": "Прочее"},
    ]
    out = _event_markers(markers, x_at=lambda i: 10.0 * i, top_y=7.0)
    assert [m["css"] for m in out] == ["up", "down", "muted"]
    assert out[0]["x"] == 0.0 and out[1]["x"] == 20.0 and out[2]["x"] == 10.0
    assert all(m["y"] == 7.0 for m in out)
    # радиус растёт с magnitude: 3.0 базовый .. 8.0 при magnitude=1.
    assert out[0]["r"] == 8.0 and out[1]["r"] == 3.0
    assert out[0]["title"] == "[earnings] Отчёт"
    assert out[2]["title"] == "Прочее"  # без type — только заголовок


def test_event_markers_empty():
    assert _event_markers(None, x_at=lambda i: i, top_y=0.0) == []


def test_sparkline_and_candles_carry_markers():
    m = [{"idx": 1, "direction": "positive", "magnitude": 0.4, "title": "X"}]
    sp = sparkline([10, 11, 12], markers=m)
    assert len(sp["markers"]) == 1 and sp["markers"][0]["title"] == "X"
    rows = [_row(2026, 1, d, 10, 11, 9, 10 + d) for d in (1, 2, 3)]
    ch = candles(rows, markers=m)
    assert len(ch["markers"]) == 1


# --- radial_graph (граф влияния) ---
def test_radial_graph_nodes_edges_and_center():
    nodes = [
        {"label": "0.80", "title": "A", "url": "http://x", "css": "up", "size": 0.8},
        {"label": "Сектор", "title": "B", "css": "gn-sector", "size": 0.0},
        {"label": "PEER", "title": "C", "css": "gn-peer", "size": 0.5},
    ]
    g = radial_graph("SBER", nodes, width=400, height=400, pad=40)
    assert g["center"]["label"] == "SBER"
    assert g["center"]["x"] == 200.0 and g["center"]["y"] == 200.0
    assert len(g["nodes"]) == 3 and len(g["edges"]) == 3
    # css/label прокидываются как есть; больший size → больший радиус
    assert [n["css"] for n in g["nodes"]] == ["up", "gn-sector", "gn-peer"]
    assert g["nodes"][0]["label"] == "0.80" and g["nodes"][1]["label"] == "Сектор"
    assert g["nodes"][0]["r"] > g["nodes"][1]["r"]
    assert g["nodes"][0]["title"] == "A" and g["nodes"][0]["url"] == "http://x"
    assert g["nodes"][1]["url"] is None
    # каждое ребро идёт из центра
    assert all(e["x1"] == 200.0 and e["y1"] == 200.0 for e in g["edges"])
    # первый узел — сверху (угол -90°): x≈центр, y выше центра
    assert abs(g["nodes"][0]["x"] - 200.0) < 0.5 and g["nodes"][0]["y"] < 200.0


def test_radial_graph_empty():
    assert radial_graph("SBER", []) is None


def test_radial_node_radius_area_proportional():
    """Площадь узла ∝ важности → (r - base) ∝ √size: size 1.0 даёт вдвое больший прирост,
    чем 0.25 (√1 / √0.25 = 2). База radial_graph = 6.0 (контракт _node_r)."""
    g = radial_graph("X", [{"label": "a", "size": 1.0}, {"label": "b", "size": 0.25}])
    big, small = g["nodes"][0]["r"], g["nodes"][1]["r"]
    assert abs((big - 6.0) - 2 * (small - 6.0)) < 0.2


# --- pie (кольцевая диаграмма аллокации) ---
def test_pie_slices_pct_sum_and_palette():
    """Доли в % суммируются к 100, цвета берутся из палитры, у каждой — path `d`."""
    p = pie([("Банки", 50.0), ("Нефтегаз", 30.0), ("Металлы", 20.0)])
    assert len(p["slices"]) == 3
    assert abs(sum(s["pct"] for s in p["slices"]) - 100.0) < 0.2
    assert all(s["d"].startswith("M") and s["color"].startswith("#") for s in p["slices"])
    # первые две доли получают разные цвета палитры
    assert p["slices"][0]["color"] != p["slices"][1]["color"]


def test_pie_empty_and_nonpositive():
    """Пусто или неположительные значения → None (шаблон покажет заглушку)."""
    assert pie([]) is None
    assert pie([("X", 0.0), ("Y", -5.0)]) is None


def test_pie_single_slice_full_ring():
    """Единственная доля 100% — полное кольцо (path с двумя полудугами), pct=100."""
    p = pie([("Всё", 42.0)])
    assert len(p["slices"]) == 1
    assert p["slices"][0]["pct"] == 100.0
    # полное кольцо: внешняя и внутренняя окружности → два под-пути (две команды M)
    assert p["slices"][0]["d"].count("M") == 2


# --- treemap (аллокация по позициям: площадь ∝ вес) ---
def test_treemap_area_proportional_and_bounds():
    """Площади прямоугольников ∝ значениям, все в пределах холста, pct≈100, цвета из палитры."""
    from geoanalytics.api.charts import treemap

    tm = treemap([("SBER", 50.0), ("GAZP", 30.0), ("LKOH", 20.0)], width=400, height=200)
    assert len(tm["rects"]) == 3
    assert abs(sum(r["pct"] for r in tm["rects"]) - 100.0) < 0.5
    areas = {r["label"]: r["w"] * r["h"] for r in tm["rects"]}
    # SBER (50%) занимает примерно в 2.5 раза больше площади, чем LKOH (20%)
    assert areas["SBER"] > areas["GAZP"] > areas["LKOH"]
    assert abs(areas["SBER"] / areas["LKOH"] - 2.5) < 0.5    # пропорция с допуском на pad
    for r in tm["rects"]:
        assert r["x"] >= 0 and r["y"] >= 0
        assert r["x"] + r["w"] <= 400.5 and r["y"] + r["h"] <= 200.5
        assert r["color"].startswith("#")


def test_treemap_empty_and_nonpositive():
    from geoanalytics.api.charts import treemap

    assert treemap([]) is None
    assert treemap([("X", 0.0), ("Y", -1.0)]) is None


# --- radial_tree (дерево влияния: корень → ветви → листья) ---
def test_radial_tree_branches_and_children():
    branches = [
        {"label": "Банки", "css": "gn-sector", "size": 0.7, "children": [
            {"label": "VTBR", "css": "gn-peer", "size": 0.4, "url": "/ui/graph?ticker=VTBR"},
            {"label": "BSPB", "css": "gn-peer", "size": 0.4},
        ]},
        {"label": "↑ 2", "css": "up", "size": 0.6, "children": [
            {"label": "0.80", "css": "up", "size": 0.8, "title": "хорошая новость"},
        ]},
        {"label": "Brent +0.30", "css": "gn-commodity", "size": 0.3},  # лист без потомков
    ]
    g = radial_tree("SBER", branches, width=400, height=400, pad=40)
    assert g["center"]["label"] == "SBER"
    # 3 ветви + 3 листа = 6 узлов; рёбер столько же (каждый узел — одно ребро к родителю)
    assert len(g["nodes"]) == 6 and len(g["edges"]) == 6
    # ветви на внутреннем кольце (ближе к центру), листья — на внешнем
    cx = cy = 200.0
    def dist(n):
        return ((n["x"] - cx) ** 2 + (n["y"] - cy) ** 2) ** 0.5
    sector = next(n for n in g["nodes"] if n["label"] == "Банки")
    peer = next(n for n in g["nodes"] if n["label"] == "VTBR")
    assert dist(peer) > dist(sector)
    # ребро листа идёт от ветви, а не из центра
    leaf_edges = [e for e in g["edges"]
                  if abs(e["x2"] - peer["x"]) < 0.2 and abs(e["y2"] - peer["y"]) < 0.2]
    assert leaf_edges and not (leaf_edges[0]["x1"] == cx and leaf_edges[0]["y1"] == cy)
    assert peer["url"] == "/ui/graph?ticker=VTBR"


def test_radial_tree_empty():
    assert radial_tree("SBER", []) is None


# --- radial_layout (большой граф: индекс → секторы → активы → события) ---
def test_radial_layout_four_levels():
    root = {"label": "IMOEX", "children": [
        {"label": "Банки", "css": "gn-sector", "size": 0.8, "children": [
            {"label": "SBER", "css": "gn-peer", "size": 0.6, "url": "/ui/graph?ticker=SBER",
             "children": [
                 {"label": "0.80", "css": "up", "size": 0.8, "title": "новость"}]},
            {"label": "VTBR", "css": "gn-peer", "size": 0.3, "children": []},
        ]},
        {"label": "Нефть и газ", "css": "gn-sector", "size": 0.7, "children": [
            {"label": "LKOH", "css": "gn-peer", "size": 0.5, "children": []}]},
    ]}
    g = radial_layout(root, width=600, height=600, pad=60)
    assert g["center"]["label"] == "IMOEX"
    # узлы: 2 сектора + 3 актива + 1 событие = 6 (корень не в nodes, он center)
    assert len(g["nodes"]) == 6
    cx = cy = 300.0
    def dist(label):
        n = next(x for x in g["nodes"] if x["label"] == label)
        return ((n["x"] - cx) ** 2 + (n["y"] - cy) ** 2) ** 0.5
    # кольца по глубине: сектор ближе актива, актив ближе события
    assert dist("Банки") < dist("SBER") < dist("0.80")
    # ссылка/css прокидываются
    sber = next(x for x in g["nodes"] if x["label"] == "SBER")
    assert sber["url"] == "/ui/graph?ticker=SBER" and sber["css"] == "gn-peer"


def test_radial_layout_empty_root():
    assert radial_layout({"label": "IMOEX", "children": []}) is None
    assert radial_layout({"label": "IMOEX"}) is None


# --- relax_overlaps (A3: анти-перекрытие узлов) ---
def _min_gap(graph):
    """Минимальный зазор между любыми двумя кругами узлов (отрицательный = пересечение)."""
    ns = graph["nodes"]
    gaps = []
    for i in range(len(ns)):
        for j in range(i + 1, len(ns)):
            d = ((ns[i]["x"] - ns[j]["x"]) ** 2 + (ns[i]["y"] - ns[j]["y"]) ** 2) ** 0.5
            gaps.append(d - ns[i]["r"] - ns[j]["r"])
    return min(gaps) if gaps else float("inf")


def _crowded_graph():
    # Много ветвей с потомками на тесном холсте → внешнее кольцо переполнено, точки слипаются.
    branches = [{"label": f"S{b}", "css": "gn-sector", "size": 0.7, "children": [
        {"label": f"P{b}_{i}", "css": "gn-peer", "size": 0.6} for i in range(7)]}
        for b in range(8)]
    return radial_tree("SBER", branches, width=360, height=360, pad=36)


def test_relax_overlaps_removes_intersections():
    from geoanalytics.api.charts import relax_overlaps

    g = _crowded_graph()
    assert _min_gap(g) < 0  # до релакса — пересечения есть
    relax_overlaps(g)
    assert _min_gap(g) >= -0.6  # после — практически без пересечений (зазор ~0)


def test_relax_overlaps_is_deterministic():
    from geoanalytics.api.charts import relax_overlaps

    a = relax_overlaps(_crowded_graph())
    b = relax_overlaps(_crowded_graph())
    assert [(n["x"], n["y"]) for n in a["nodes"]] == [(n["x"], n["y"]) for n in b["nodes"]]


def test_relax_overlaps_edges_follow_nodes():
    from geoanalytics.api.charts import relax_overlaps

    g = _crowded_graph()
    relax_overlaps(g)
    # Конец ребра-листа должен совпадать с новой позицией своего узла (рёбра переподключены).
    peer = next(n for n in g["nodes"] if n["label"] == "P3_3")
    assert any(abs(e["x2"] - peer["x"]) < 0.2 and abs(e["y2"] - peer["y"]) < 0.2
               for e in g["edges"])


def test_relax_overlaps_keeps_labels_offset():
    from geoanalytics.api.charts import relax_overlaps

    g = _crowded_graph()
    before = {n["label"]: (n["lx"] - n["x"], n["ly"] - n["y"]) for n in g["nodes"]}
    relax_overlaps(g)
    after = {n["label"]: (round(n["lx"] - n["x"], 1), round(n["ly"] - n["y"], 1))
             for n in g["nodes"]}
    # Дельта подписи (стиль раскладки внутрь/наружу) сохраняется жёстким сдвигом.
    for lbl, (dx, dy) in before.items():
        assert abs(after[lbl][0] - dx) < 0.2 and abs(after[lbl][1] - dy) < 0.2


def test_relax_overlaps_handles_none_and_empty():
    from geoanalytics.api.charts import relax_overlaps

    assert relax_overlaps(None) is None
    assert relax_overlaps({"nodes": [], "edges": [], "center": {"x": 0, "y": 0},
                           "width": 10, "height": 10})["nodes"] == []
