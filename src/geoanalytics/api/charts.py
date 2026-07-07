"""Серверные SVG-графики для дашборда (M5.2, расширено в M6).

Чистые функции: ряды чисел/свечей → координаты для inline-SVG. Так дашборд не тянет
JS-библиотек и сборку — график рисуется прямо в HTML и легко тестируется.

- `sparkline` — линия по ряду закрытий (+ опциональные подписи дат под осью X);
- `candles`   — японские свечи по OHLC-ряду;
- `date_labels` — равномерные подписи дат для оси X (общие для обоих типов графика).

И тот и другой принимают `overlays` — наложенные на цену ряды (SMA, полосы Bollinger):
список ``{name, values, css, dash}``, где `values` выровнен по барам (None в прогреве
индикатора пропускается). Оверлеи рисуются в той же системе координат, поэтому диапазон
оси Y расширяется, чтобы вместить их (полоса Bollinger может выходить за экстремумы цены).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime

from geoanalytics.analytics.indicators import rsi


def _overlay_range(lo: float, hi: float, overlays: list[dict] | None) -> tuple[float, float]:
    """Расширить диапазон [lo, hi], чтобы вместить значения всех оверлеев."""
    if not overlays:
        return lo, hi
    vals = [v for ov in overlays for v in ov.get("values", []) if v is not None]
    if vals:
        lo, hi = min(lo, min(vals)), max(hi, max(vals))
    return lo, hi


def _overlay_lines(overlays: list[dict] | None, lo: float, hi: float, height: int, pad: int,
                   x_at) -> list[dict]:
    """Полилинии оверлеев в координатах графика (None-точки прогрева пропущены).

    `x_at(i)` — функция X-координаты бара i (своя у линии и у свечей). Возвращает
    список ``{name, points, css, dash}``; ряды без точек (целиком прогрев) отброшены.
    """
    span = (hi - lo) or 1.0
    inner_h = height - 2 * pad
    out: list[dict] = []
    for ov in overlays or []:
        pts = [
            f"{x_at(i):.1f},{pad + inner_h * (1 - (v - lo) / span):.1f}"
            for i, v in enumerate(ov.get("values", [])) if v is not None
        ]
        if pts:
            out.append({"name": ov["name"], "points": " ".join(pts),
                        "css": ov.get("css", "var(--muted)"), "dash": ov.get("dash", "")})
    return out


def _event_markers(markers: list[dict] | None, x_at, top_y: float) -> list[dict]:
    """Маркеры событий над графиком (#5): по точке на событие у своего бара.

    Вход — ``[{idx, direction, magnitude, title, type}]`` (idx — индекс бара, его X даёт
    `x_at`). Цвет по направлению (css up/down/flat), радиус растёт с magnitude, tooltip =
    тип+заголовок. Возвращает ``[{x, y, r, css, title}]`` для шаблона (кружки с <title>).
    """
    out: list[dict] = []
    for m in markers or []:
        mag = min(max(float(m.get("magnitude") or 0.0), 0.0), 1.0)
        css = {"positive": "up", "negative": "down"}.get(m.get("direction"), "muted")
        title = m.get("title") or ""
        if m.get("type"):
            title = f"[{m['type']}] {title}"
        out.append({"x": round(x_at(m["idx"]), 1), "y": round(top_y, 1),
                    "r": round(3.0 + 5.0 * mag, 1), "css": css, "title": title})
    return out


def date_labels(timestamps: Sequence[datetime], width: int = 640, pad: int = 6,
                count: int = 5) -> list[dict]:
    """Несколько равномерных подписей дат для оси X: список ``{x, text}``."""
    n = len(timestamps)
    if n == 0:
        return []
    inner_w = width - 2 * pad
    if n == 1:
        return [{"x": round(pad + inner_w / 2, 1), "text": timestamps[0].strftime("%d.%m.%y")}]
    step = max(1, (n - 1) // max(1, count - 1))
    idxs = list(range(0, n, step))
    if idxs[-1] != n - 1:
        idxs.append(n - 1)
    return [
        {"x": round(pad + inner_w * i / (n - 1), 1), "text": timestamps[i].strftime("%d.%m.%y")}
        for i in idxs
    ]


def _xhair(lo: float, hi: float, pad: int, width: int, height: int,
           kind: str, bars: list[dict]) -> dict:
    """Данные для перекрестия-курсора (как в торговом терминале): диапазон оси Y (для
    интерполяции цены под курсором) + массив баров (снап по X). Чистые числа — клиентский JS
    в `base.html` рисует оверлей. `lo/hi` — расширенный под оверлеи диапазон координат (тот
    же, по которому нарисована линия/свечи), поэтому пиксель↔цена совпадает с графиком."""
    return {"lo": round(lo, 4), "hi": round(hi, 4), "pad": pad,
            "w": width, "h": height, "kind": kind, "bars": bars}


def sparkline(values: list[float], width: int = 640, height: int = 140,
              pad: int = 6, labels: list[dict] | None = None,
              overlays: list[dict] | None = None,
              markers: list[dict] | None = None,
              dates: Sequence[datetime] | None = None) -> dict | None:
    """Координаты полилинии для ряда значений.

    Возвращает словарь для шаблона (`points`, размеры, min/max/first/last, `up`, `labels`,
    `overlays`) либо None, если данных недостаточно (< 2 точек) — тогда шаблон покажет
    заглушку. Ось Y инвертирована под систему координат SVG (начало — верхний левый угол).
    `overlays` — наложенные ряды (SMA/Bollinger), см. модульный докстринг. `dates` (опц.) —
    даты баров; если заданы, в `xhair.bars[*].t` кладётся подпись для перекрестия-курсора.
    """
    if not values or len(values) < 2:
        return None
    plo, phi = min(values), max(values)            # диапазон цены (для подписи)
    lo, hi = _overlay_range(plo, phi, overlays)    # расширенный — для координат
    span = (hi - lo) or 1.0
    inner_w = width - 2 * pad
    inner_h = height - 2 * pad
    n = len(values)

    def x_at(i: int) -> float:
        return pad + inner_w * i / (n - 1)

    def y_at(v: float) -> float:
        return pad + inner_h * (1 - (v - lo) / span)

    points = [f"{x_at(i):.1f},{y_at(v):.1f}" for i, v in enumerate(values)]
    bars = [{"x": round(x_at(i), 1), "y": round(y_at(v), 1), "v": round(v, 2),
             "t": dates[i].strftime("%d.%m.%y") if dates and i < len(dates) else ""}
            for i, v in enumerate(values)]
    return {
        "points": " ".join(points),
        "n": n,
        "width": width,
        "height": height,
        "min": round(plo, 2),
        "max": round(phi, 2),
        "first": round(values[0], 2),
        "last": round(values[-1], 2),
        "up": values[-1] >= values[0],
        "labels": labels or [],
        "overlays": _overlay_lines(overlays, lo, hi, height, pad, x_at),
        "markers": _event_markers(markers, x_at, pad + 7),
        "xhair": _xhair(lo, hi, pad, width, height, "line", bars),
    }


def candles(rows: list[tuple], width: int = 640, height: int = 160,
            pad: int = 6, labels: list[dict] | None = None,
            overlays: list[dict] | None = None,
            markers: list[dict] | None = None) -> dict | None:
    """Геометрия японских свечей по OHLC-ряду ``[(ts, open, high, low, close), ...]``.

    Каждая свеча: тело-прямоугольник (open..close) и фитиль-линия (low..high). Цвет —
    по росту/падению (`up`). Возвращает None, если свечей нет. `overlays` — наложенные
    ряды (SMA/Bollinger), см. модульный докстринг.
    """
    if not rows:
        return None
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]
    plo, phi = min(lows), max(highs)               # диапазон цены (для подписи)
    lo, hi = _overlay_range(plo, phi, overlays)    # расширенный — для координат
    span = (hi - lo) or 1.0
    inner_w = width - 2 * pad
    inner_h = height - 2 * pad
    n = len(rows)
    slot = inner_w / n
    body_w = max(1.5, slot * 0.6)

    def x_at(i: int) -> float:
        return pad + slot * (i + 0.5)

    def y(v: float) -> float:
        return pad + inner_h * (1 - (v - lo) / span)

    out = []
    bars = []
    for i, (_ts, o, h, low, c) in enumerate(rows):
        xc = x_at(i)
        body_top = y(max(o, c))
        body_bot = y(min(o, c))
        out.append({
            "x": round(xc - body_w / 2, 1),
            "w": round(body_w, 1),
            "y": round(body_top, 1),
            "h": round(max(1.0, body_bot - body_top), 1),
            "cx": round(xc, 1),
            "wick_top": round(y(h), 1),
            "wick_bot": round(y(low), 1),
            "up": c >= o,
        })
        bars.append({"x": round(xc, 1), "y": round(y(c), 1),
                     "o": round(o, 2), "h": round(h, 2), "l": round(low, 2),
                     "c": round(c, 2), "up": c >= o,
                     "t": _ts.strftime("%d.%m.%y") if hasattr(_ts, "strftime") else ""})
    return {
        "candles": out,
        "n": n,
        "width": width,
        "height": height,
        "min": round(plo, 2),
        "max": round(phi, 2),
        "first": round(rows[0][4], 2),
        "last": round(rows[-1][4], 2),
        "up": rows[-1][4] >= rows[0][4],
        "labels": labels or [],
        "overlays": _overlay_lines(overlays, lo, hi, height, pad, x_at),
        "markers": _event_markers(markers, x_at, pad + 7),
        "xhair": _xhair(lo, hi, pad, width, height, "candles", bars),
    }


def volume_bars(volumes: list[float | None], ups: list[bool], width: int = 640,
                height: int = 60, pad: int = 6) -> dict | None:
    """Сабпанель объёма (C2): столбики объёма, цвет по направлению дня (рост/падение).

    `volumes` выровнен с барами цены (None → нулевая высота); `ups[i]` — закрытие ≥ открытия
    (зелёный/красный). Высота столбика нормирована к максимуму. None, если объёма нет вовсе.
    """
    vals = [v for v in volumes if v is not None and v > 0]
    if not vals or max(vals) <= 0:
        return None
    # Робастная нормировка: масштаб — по 95-му перцентилю, а не по максимуму. Иначе один
    # объёмный всплеск (день отчётности и т.п.) делает все обычные столбики под-пиксельными —
    # панель вырождалась в пунктир у нуля. Всплески клипуются на всю высоту (видно, что «много»).
    svals = sorted(vals)
    hi = svals[min(len(svals) - 1, int(0.95 * len(svals)))] or max(vals)
    real_max = max(vals)
    inner_w = width - 2 * pad
    inner_h = height - 2 * pad
    n = len(volumes)
    slot = inner_w / n
    body_w = max(1.0, slot * 0.7)
    bars = []
    for i, v in enumerate(volumes):
        if v is None or v <= 0:
            continue
        h = inner_h * min(v, hi) / hi          # клип на 95-й перцентиль → всплеск = полная высота
        xc = pad + slot * (i + 0.5)
        bars.append({"x": round(xc - body_w / 2, 1), "w": round(body_w, 1),
                     "y": round(pad + inner_h - h, 1), "h": round(max(1.0, h), 1),
                     "up": bool(ups[i]) if i < len(ups) else True,
                     "clip": v > hi})              # столбик-всплеск (можно подсветить в шаблоне)
    return {"bars": bars, "width": width, "height": height, "max": round(real_max, 2)}


def rsi_panel(closes: list[float], width: int = 640, height: int = 70, pad: int = 6,
              window: int = 14, low: float = 30.0, high: float = 70.0) -> dict | None:
    """Сабпанель осциллятора RSI (C3): линия RSI 0..100 с уровнями перепрод./перекупл.

    RSI считается на каждый бар (None в прогреве пропускается); шкала фиксирована 0..100,
    поэтому уровни `low`/`high` (30/70) — горизонтальные линии-ориентиры. None, если RSI ещё
    нигде не определён (данных < window+1).
    """
    inner_w = width - 2 * pad
    inner_h = height - 2 * pad
    n = len(closes)

    def y_at(v: float) -> float:
        return pad + inner_h * (1 - v / 100.0)

    pts = []
    for i in range(n):
        r = rsi(closes[:i + 1], window)
        if r is None:
            continue
        x = pad + (inner_w * i / (n - 1) if n > 1 else inner_w / 2)
        pts.append(f"{x:.1f},{y_at(r):.1f}")
    if not pts:
        return None
    last = rsi(closes, window)
    return {
        "points": " ".join(pts), "width": width, "height": height,
        "low": low, "high": high,
        "low_y": round(y_at(low), 1), "high_y": round(y_at(high), 1),
        "last": round(last, 2) if last is not None else None,
    }


def sentiment_strip(cells: list[dict], width: int = 640, height: int = 24) -> dict | None:
    """Цветовая полоса тональности во времени (C5): по ячейке на день.

    `cells` — ``[{label, score}]`` в хронологическом порядке, score ∈ [-1, 1] (среднее за
    день). Цвет: зелёный (позитив) / красный (негатив) / серый (нейтрально); насыщенность
    растёт с |score|. None, если ячеек нет.
    """
    if not cells:
        return None
    n = len(cells)
    w = width / n
    out = []
    for i, c in enumerate(cells):
        score = max(-1.0, min(1.0, float(c.get("score", 0.0))))
        cls = "up" if score > 0.05 else "down" if score < -0.05 else "flat"
        out.append({"x": round(i * w, 2), "w": round(w, 2), "cls": cls,
                    "opacity": round(0.25 + 0.75 * abs(score), 2),
                    "score": round(score, 3), "label": c.get("label", "")})
    return {"cells": out, "width": width, "height": height, "n": n,
            "first": cells[0].get("label", ""), "last": cells[-1].get("label", "")}


def equity_chart(equity: list[float], trades: list, width: int = 640, height: int = 160,
                 pad: int = 6) -> dict | None:
    """Кривая капитала с маркерами сделок и заливкой просадки (C4).

    Возвращает: `points` (линия equity), `dd_fill` (полигон «под водой» — между бегущим
    пиком сверху и equity снизу, показывает просадки), `markers` (точки входа/выхода сделок;
    `kind` = buy/sell). `trades` — объекты с `entry_idx`/`exit_idx` (индексы баров; equity той
    же длины, что ряд закрытий). None, если кривой нет (< 2 точек).
    """
    if not equity or len(equity) < 2:
        return None
    lo, hi = min(equity), max(equity)
    span = (hi - lo) or 1.0
    inner_w = width - 2 * pad
    inner_h = height - 2 * pad
    n = len(equity)

    def x_at(i: int) -> float:
        return pad + inner_w * i / (n - 1)

    def y_at(v: float) -> float:
        return pad + inner_h * (1 - (v - lo) / span)

    points = " ".join(f"{x_at(i):.1f},{y_at(v):.1f}" for i, v in enumerate(equity))

    # Бегущий пик → заливка «под водой» (полигон: пик слева-направо, затем equity обратно).
    peak, p = [], equity[0]
    for v in equity:
        p = max(p, v)
        peak.append(p)
    top = [f"{x_at(i):.1f},{y_at(pk):.1f}" for i, pk in enumerate(peak)]
    bot = [f"{x_at(i):.1f},{y_at(equity[i]):.1f}" for i in range(n - 1, -1, -1)]
    dd_fill = " ".join(top + bot)

    markers = []
    for t in trades or []:
        for idx, kind in ((t.entry_idx, "buy"), (t.exit_idx, "sell")):
            if 0 <= idx < n:
                markers.append({"x": round(x_at(idx), 1), "y": round(y_at(equity[idx]), 1),
                                "kind": kind})
    return {
        "points": points, "dd_fill": dd_fill, "markers": markers,
        "width": width, "height": height, "up": equity[-1] >= equity[0],
        "min": round(lo, 4), "max": round(hi, 4), "last": round(equity[-1], 4),
    }


# Палитра долей пирога — цикл по слоям (CSS-переменные темы; см. base.html `--cat-*`).
_PIE_PALETTE = ("#4f9fff", "#48c78e", "#ffb454", "#e06c75", "#a78bfa",
                "#2dd4bf", "#f472b6", "#94a3b8")


def pie(slices: list[tuple[str, float]], size: int = 220, inner: float = 0.56,
        pad: int = 6) -> dict | None:
    """Кольцевая диаграмма (donut) долей: список ``(label, value)`` → дуги-сектора.

    Возвращает ``{size, slices:[{d, label, pct, color}]}`` (path `d` — сектор кольца) либо
    None при пустых/неположительных данных. Доли упорядочены как пришли; цвет — циклом по
    палитре. Единственная доля 100% рисуется полным кольцом (двумя полудугами — иначе дуга
    360° вырождается)."""
    pos = [(lbl, float(v)) for lbl, v in slices if v and v > 0]
    total = sum(v for _, v in pos)
    if total <= 0:
        return None
    cx = cy = size / 2.0
    r = (size - 2 * pad) / 2.0
    ri = r * inner

    def pt(rad: float, ang: float) -> tuple[float, float]:
        return cx + rad * math.sin(ang), cy - rad * math.cos(ang)

    out: list[dict] = []
    a0 = 0.0
    for i, (label, v) in enumerate(pos):
        frac = v / total
        color = _PIE_PALETTE[i % len(_PIE_PALETTE)]
        if frac >= 0.9999:  # единственный сектор — полное кольцо двумя полудугами
            xa, ya = pt(r, 0.0)
            xb, yb = pt(r, math.pi)
            xc, yc = pt(ri, math.pi)
            xd, yd = pt(ri, 0.0)
            d = (f"M{xa:.1f},{ya:.1f} A{r:.1f},{r:.1f} 0 1 1 {xb:.1f},{yb:.1f} "
                 f"A{r:.1f},{r:.1f} 0 1 1 {xa:.1f},{ya:.1f} Z "
                 f"M{xd:.1f},{yd:.1f} A{ri:.1f},{ri:.1f} 0 1 0 {xc:.1f},{yc:.1f} "
                 f"A{ri:.1f},{ri:.1f} 0 1 0 {xd:.1f},{yd:.1f} Z")
            out.append({"d": d, "label": label, "pct": round(frac * 100, 1), "color": color})
            break
        a1 = a0 + frac * 2 * math.pi
        large = 1 if (a1 - a0) > math.pi else 0
        xo0, yo0 = pt(r, a0)
        xo1, yo1 = pt(r, a1)
        xi1, yi1 = pt(ri, a1)
        xi0, yi0 = pt(ri, a0)
        d = (f"M{xo0:.1f},{yo0:.1f} A{r:.1f},{r:.1f} 0 {large} 1 {xo1:.1f},{yo1:.1f} "
             f"L{xi1:.1f},{yi1:.1f} A{ri:.1f},{ri:.1f} 0 {large} 0 {xi0:.1f},{yi0:.1f} Z")
        out.append({"d": d, "label": label, "pct": round(frac * 100, 1), "color": color})
        a0 = a1
    return {"size": size, "slices": out}


def _worst_ratio(areas: list[float], length: float) -> float:
    """Худшее (макс) соотношение сторон прямоугольников ряда при укладке вдоль `length`
    (алгоритм squarify, Bruls и др.). Чем ближе к 1 — тем «квадратнее». Чистая."""
    s = sum(areas)
    if s <= 0 or length <= 0:
        return float("inf")
    rmax, rmin = max(areas), min(areas)
    return max(length * length * rmax / (s * s), s * s / (length * length * rmin))


def _squarify(items: list[tuple], x0: float, y0: float, width: float,
              height: float) -> list[tuple]:
    """Squarify-укладка (Bruls и др.): ``[(payload, value)]`` → ``[(payload, x, y, w, h)]``.

    Раскладывает прямоугольники с ПЛОЩАДЬЮ ∝ value в область (x0,y0,width,height), стремясь к
    квадратности (минимизирует худшее соотношение сторон). Чистая геометрия — переиспользуется и
    плоским `treemap`, и двухуровневой картой рынка `market_heatmap`. Нулевые/отрицательные value
    отбрасываются. Порядок входа сохраняется как есть (сортируй до вызова при желании)."""
    pos = [(p, float(v)) for p, v in items if v and v > 0]
    total = sum(v for _, v in pos)
    placed: list[tuple] = []
    if total <= 0 or width <= 0 or height <= 0:
        return placed
    scale = (width * height) / total
    queue = [(p, v, v * scale) for p, v in pos]          # (payload, value, area)
    x, y = float(x0), float(y0)
    w, h = float(width), float(height)
    while queue:
        length = min(w, h)
        row: list[tuple] = []
        i = 0
        while i < len(queue):
            cand = [a for *_r, a in row] + [queue[i][2]]
            if row and _worst_ratio(cand, length) > _worst_ratio([a for *_r, a in row], length):
                break
            row.append(queue[i])
            i += 1
        s = sum(a for *_r, a in row)
        thick = s / length if length else 0.0
        off = y if w >= h else x
        for p, _v, area in row:
            extent = area / thick if thick else 0.0
            if w >= h:
                placed.append((p, x, off, thick, extent))
            else:
                placed.append((p, off, y, extent, thick))
            off += extent
        if w >= h:
            x += thick
            w -= thick
        else:
            y += thick
            h -= thick
        queue = queue[i:]
    return placed


def _heat_color(pct: float | None) -> tuple[str, float]:
    """Цвет ячейки тепловой карты по дневному изменению: (css-класс, прозрачность).

    Финвиз-конвенция: зелёный — рост, красный — падение, серый — около нуля; насыщенность
    растёт с |изменение| и насыщается к ±3%. На тёмном фоне прозрачность даёт градиент.
    """
    p = max(-3.0, min(3.0, float(pct or 0.0)))
    if abs(p) < 0.1:
        return "flat", 0.35
    css = "up" if p > 0 else "down"
    return css, round(0.35 + 0.65 * min(abs(p) / 3.0, 1.0), 2)


def market_heatmap(sectors: list[dict], width: int = 1600, height: int = 720,
                   gap: float = 6.0, lblh: float = 19.0) -> dict | None:
    """Карта рынка (Finviz/smart-lab): секторы-блоки, внутри активы; площадь ∝ объёму, цвет — Δ%.

    Двухуровневый squarify: сначала секторы по суммарному объёму, затем активы внутри каждого
    сектора. Вход — ``[{label, items:[{label, value, pct}]}]`` (value=оборот/объём, pct=дневное
    изменение, %). Подпись тикера/процента — по ЦЕНТРУ плитки, кегль масштабируется под её размер
    (крупная плитка → крупный тикер). Возвращает ``{width, height, cells, sectors}`` или None.
    """
    groups = [(s, sum(float(it.get("value") or 0.0) for it in s.get("items") or []))
              for s in sectors]
    groups = sorted((g for g in groups if g[1] > 0), key=lambda g: g[1], reverse=True)
    if not groups:
        return None
    sector_rects = _squarify(groups, 0.0, 0.0, width, height)
    cells: list[dict] = []
    labels: list[dict] = []
    for s, sx, sy, sw, sh in sector_rects:
        if sw > 64:                                # подпись узкого сектора не влезает — прячем
            labels.append({"x": round(sx + 6, 1), "y": round(sy + 13, 1),
                           "label": s["label"], "w": round(sw, 1)})
        ix, iy = sx + gap / 2, sy + lblh           # под подпись сектора сверху
        iw, ih = sw - gap, sh - lblh - gap / 2
        items = sorted(((it, float(it.get("value") or 0.0)) for it in s["items"]
                        if (it.get("value") or 0.0) > 0), key=lambda t: t[1], reverse=True)
        for it, rx, ry, rw, rh in _squarify(items, ix, iy, max(iw, 1.0), max(ih, 1.0)):
            css, op = _heat_color(it.get("pct"))
            w, h = max(rw - 2, 0.0), max(rh - 2, 0.0)
            # Кегль тикера ∝ размеру плитки (как на биржевых картах): крупная плитка — крупный
            # тикер, мелкая — мелкий; ограничен снизу/сверху. Процент — ~0.6 кегля тикера.
            fs = max(9.0, min(min(w, h) * 0.34, 40.0))
            show = w > 24 and h > 16
            show_pct = show and h > fs * 2.4 and w > fs * 2.6 and it.get("pct") is not None
            cells.append({
                "x": round(rx + 1, 1), "y": round(ry + 1, 1),
                "w": round(w, 1), "h": round(h, 1),
                "cx": round(rx + 1 + w / 2, 1), "cy": round(ry + 1 + h / 2, 1),
                "label": it["label"], "pct": it.get("pct"),
                "css": css, "opacity": op,
                "fs": round(fs, 1), "fs_pct": round(fs * 0.62, 1),
                "show": show, "show_pct": show_pct})
    return {"width": width, "height": height, "cells": cells, "sectors": labels}


def treemap(items: list[tuple[str, float]], width: int = 820, height: int = 240,
            pad: int = 2) -> dict | None:
    """Squarified-treemap долей: ``[(label, value)]`` → прямоугольники, ПЛОЩАДЬ ∝ value.

    Возвращает ``{width, height, rects:[{x,y,w,h,label,pct,color}]}`` либо None при пустых
    данных. Площадь читается глазом точнее длины дуги — для аллокации портфеля нагляднее pie на
    многих позициях. Цвет — циклом по палитре (как donut). Чистая (без БД)."""
    pos = sorted(((lbl, float(v)) for lbl, v in items if v and v > 0),
                 key=lambda x: x[1], reverse=True)
    total = sum(v for _, v in pos)
    if total <= 0:
        return None
    values = dict(pos)
    rects = []
    for i, (lbl, rx, ry, rw, rh) in enumerate(_squarify(pos, 0.0, 0.0, width, height)):
        rects.append({
            "x": round(rx + pad / 2, 1), "y": round(ry + pad / 2, 1),
            "w": round(max(rw - pad, 0.0), 1), "h": round(max(rh - pad, 0.0), 1),
            "label": lbl, "pct": round(values[lbl] / total * 100, 1),
            "color": _PIE_PALETTE[i % len(_PIE_PALETTE)],
        })
    return {"width": width, "height": height, "rects": rects}


def _node_r(size, base: float, span: float) -> float:
    """Радиус узла по ПЛОЩАДИ (Флэннери): площадь ∝ важности → радиус = base + span·√size.

    Глаз читает площадь круга, поэтому линейный радиус (base + span·size) раздувал бы
    разницу важности квадратично — близкие по силе узлы казались бы несопоставимо разными.
    `size` ∈ [0,1] — нормированная важность (magnitude / |корреляция| / давление). Чистая.
    """
    s = min(max(float(size or 0.0), 0.0), 1.0)
    return base + span * math.sqrt(s)


def radial_graph(center_label: str, nodes: list[dict], width: int = 820,
                 height: int = 620, pad: int = 90) -> dict | None:
    """Радиальная звезда: центр-актив + типизированные узлы на кольце (граф влияния).

    Узлы любого типа (события/сектор/пиры) рисуются точками вокруг центра, каждый соединён
    линией с центром (дерево). Семантику готовит вызывающий, функция — чисто геометрия.

    Вход `nodes` — ``[{label, title, url, css, size}]``: `label` — постоянная подпись у точки,
    `title` — всплывающая подсказка, `url` — ссылка (или None), `css` — суффикс цвета
    (var(--<css>): up/down/muted/gn-sector/gn-peer), `size` ∈ [0,1] — радиус. Возвращает
    геометрию SVG (`center`, `edges`, `nodes`) или None, если узлов нет. Детерминированная.
    """
    if not nodes:
        return None
    cx, cy = width / 2, height / 2
    radius = min(width, height) / 2 - pad
    n = len(nodes)
    start = -math.pi / 2  # первый узел — сверху
    out_nodes = []
    edges = []
    for i, nd in enumerate(nodes):
        ang = start + 2 * math.pi * i / n
        x = cx + radius * math.cos(ang)
        y = cy + radius * math.sin(ang)
        ca = math.cos(ang)
        anchor = "start" if ca > 0.3 else "end" if ca < -0.3 else "middle"
        out_nodes.append({
            "x": round(x, 1), "y": round(y, 1), "r": round(_node_r(nd.get("size"), 6.0, 12.0), 1),
            "css": nd.get("css") or "muted", "label": nd.get("label") or "",
            "title": nd.get("title") or "", "url": nd.get("url"),
            "lx": round(cx + (radius + 16) * math.cos(ang), 1),
            "ly": round(cy + (radius + 16) * math.sin(ang) + 4, 1),
            "anchor": anchor,
        })
        edges.append({"x1": round(cx, 1), "y1": round(cy, 1),
                      "x2": round(x, 1), "y2": round(y, 1)})
    return {
        "width": width, "height": height,
        "center": {"x": round(cx, 1), "y": round(cy, 1), "r": 24.0, "label": center_label},
        "edges": edges, "nodes": out_nodes,
    }


def radial_tree(root_label: str, branches: list[dict], width: int = 1000,
                height: int = 760, pad: int = 132) -> dict | None:
    """Радиальное ДЕРЕВО: корень в центре, ветви на внутреннем кольце, их листья — на внешнем.

    В отличие от плоской `radial_graph` (всё в центр) узлы группируются: ветвь-агрегат
    (сектор, «↑ позитивные события», фактор) сидит на внутреннем кольце и тянет ребро к
    корню; её потомки (пиры, отдельные события) — на внешнем кольце, ребро от ветви, не от
    центра. Ветвь без потомков — лист прямо на внутреннем кольце (фактор влияния).

    Вход `branches` — ``[{label, title, url, css, size, children:[{label,title,url,css,size}]}]``.
    Угловая доля ветви ∝ (1 + число потомков), чтобы насыщенные ветви получали больше места.
    Возвращает тот же контракт, что `radial_graph` (`center`/`edges`/`nodes`) — шаблон общий.
    Детерминированная (порядок ветвей/потомков сохраняется, первая ветвь — сверху).
    """
    if not branches:
        return None
    cx, cy = width / 2, height / 2
    rmax = min(width, height) / 2 - pad
    r1 = rmax * 0.6   # внутреннее кольцо — ветви/агрегаты
    r2 = rmax         # внешнее кольцо — листья
    weights = [1 + len(b.get("children") or []) for b in branches]
    total = sum(weights) or 1
    nodes: list[dict] = []
    edges: list[dict] = []
    start = -math.pi / 2
    acc = 0.0

    def _anchor(c: float) -> str:
        return "start" if c > 0.3 else "end" if c < -0.3 else "middle"

    for b, w in zip(branches, weights, strict=True):
        a0 = start + 2 * math.pi * acc / total
        a1 = start + 2 * math.pi * (acc + w) / total
        acc += w
        amid = (a0 + a1) / 2
        bx = cx + r1 * math.cos(amid)
        by = cy + r1 * math.sin(amid)
        br = _node_r(b.get("size"), 8.0, 12.0)
        lr = r1 - br - 8       # подпись ветви — внутрь, в пустое кольцо у центра
        nodes.append({
            "x": round(bx, 1), "y": round(by, 1), "r": round(br, 1),
            "css": b.get("css") or "muted", "label": b.get("label") or "",
            "title": b.get("title") or "", "url": b.get("url"),
            "lx": round(cx + lr * math.cos(amid), 1),
            "ly": round(cy + lr * math.sin(amid) + 4, 1),
            "anchor": _anchor(math.cos(amid)),
        })
        edges.append({"x1": round(cx, 1), "y1": round(cy, 1),
                      "x2": round(bx, 1), "y2": round(by, 1)})
        children = b.get("children") or []
        m = len(children)
        for j, ch in enumerate(children):
            cang = a0 + (a1 - a0) * (j + 0.5) / m
            chx = cx + r2 * math.cos(cang)
            chy = cy + r2 * math.sin(cang)
            nodes.append({
                "x": round(chx, 1), "y": round(chy, 1),
                "r": round(_node_r(ch.get("size"), 5.0, 9.0), 1),
                "css": ch.get("css") or "muted", "label": ch.get("label") or "",
                "title": ch.get("title") or "", "url": ch.get("url"),
                "lx": round(cx + (r2 + 16) * math.cos(cang), 1),
                "ly": round(cy + (r2 + 16) * math.sin(cang) + 4, 1),
                "anchor": _anchor(math.cos(cang)),
            })
            edges.append({"x1": round(bx, 1), "y1": round(by, 1),
                          "x2": round(chx, 1), "y2": round(chy, 1)})
    return {
        "width": width, "height": height,
        "center": {"x": round(cx, 1), "y": round(cy, 1), "r": 24.0, "label": root_label},
        "edges": edges, "nodes": nodes,
    }


def _leaf_count(node: dict) -> int:
    """Число листьев в поддереве (вес для угловой доли узла)."""
    ch = node.get("children") or []
    return sum(_leaf_count(c) for c in ch) if ch else 1


def _node_depth(node: dict) -> int:
    """Глубина поддерева в рёбрах (лист = 0); у корня = число колец графа."""
    ch = node.get("children") or []
    return 1 + max((_node_depth(c) for c in ch), default=-1) if ch else 0


def radial_layout(root: dict, width: int = 1220, height: int = 1220,
                  pad: int = 160) -> dict | None:
    """Многоуровневое радиальное дерево произвольной глубины (большой граф IMOEX, B).

    Обобщение `radial_tree` на N уровней: корень в центре, каждый последующий уровень —
    на своём кольце (radius = depth × ring). Угловой сектор узла ∝ числу его листьев
    (`_leaf_count`) → насыщенные ветви получают больше места и не слипаются. Ребро ведёт от
    родителя к потомку (не из центра), подпись — снаружи точки по радиусу.

    Вход — вложенный `root` ``{label, title, url, css, size, children:[…такие же…]}``; листья
    без `children`. Возвращает тот же контракт (`center`/`edges`/`nodes`), что `radial_graph`/
    `radial_tree` → шаблон `_graph_svg.html` общий. None, если у корня нет потомков.
    Детерминированная (порядок ветвей/потомков сохраняется, первый — сверху).
    """
    children = root.get("children") or []
    if not children:
        return None
    cx, cy = width / 2, height / 2
    rmax = min(width, height) / 2 - pad
    ring = rmax / (_node_depth(root) or 1)
    nodes: list[dict] = []
    edges: list[dict] = []

    def place(node: dict, a0: float, a1: float, d: int, px: float, py: float) -> None:
        amid = (a0 + a1) / 2
        if d == 0:
            x, y = cx, cy
        else:
            r = ring * d
            x, y = cx + r * math.cos(amid), cy + r * math.sin(amid)
            nr = _node_r(node.get("size"), 5.0, 10.0)
            ca, sa = math.cos(amid), math.sin(amid)
            nodes.append({
                "x": round(x, 1), "y": round(y, 1), "r": round(nr, 1),
                "css": node.get("css") or "muted", "label": node.get("label") or "",
                "title": node.get("title") or "", "url": node.get("url"),
                "lx": round(x + (nr + 4) * ca, 1), "ly": round(y + (nr + 4) * sa + 4, 1),
                "anchor": "start" if ca > 0.3 else "end" if ca < -0.3 else "middle",
            })
            edges.append({"x1": round(px, 1), "y1": round(py, 1),
                          "x2": round(x, 1), "y2": round(y, 1)})
        ch = node.get("children") or []
        if ch:
            weights = [_leaf_count(c) for c in ch]
            total = sum(weights) or 1
            acc = 0.0
            for c, w in zip(ch, weights, strict=True):
                place(c, a0 + (a1 - a0) * acc / total,
                      a0 + (a1 - a0) * (acc + w) / total, d + 1, x, y)
                acc += w

    place(root, -math.pi / 2, -math.pi / 2 + 2 * math.pi, 0, cx, cy)
    return {
        "width": width, "height": height,
        "center": {"x": round(cx, 1), "y": round(cy, 1), "r": 24.0,
                   "label": root.get("label") or ""},
        "edges": edges, "nodes": nodes,
    }


def relax_overlaps(graph: dict | None, iters: int = 80, pad: float = 4.0,
                   spring: float = 0.08) -> dict | None:
    """Анти-перекрытие узлов графа: лёгкий force-relax поверх радиальной раскладки (A3).

    Проблема: `radial_tree`/`radial_layout` кладут узлы на фиксированные кольца — при насыщении
    точки налезают друг на друга. Здесь — детерминированный пост-процесс (без JS-библиотек, в
    духе серверного SVG): попарное отталкивание при пересечении кругов (с зазором `pad`),
    отталкивание от центрального хаба и пружина к ИСХОДНОЙ радиальной позиции (сохраняет
    семантику колец и не даёт узлам разлетаться). «Масса» узла ∝ его радиусу (а тот ∝ √важности,
    см. `_node_r`): тяжёлые/важные двигаются меньше и держат место, лёгкие — расталкиваются.

    Контракт graph не меняется (`center`/`edges`/`nodes`). Подписи переносятся жёстким сдвигом
    относительно узла (стиль каждой раскладки — внутрь/наружу — сохраняется), рёбра
    переподключаются к новым позициям своих концов. Детерминированно (фиксированный порядок,
    без random). None/пустой граф — без изменений.
    """
    if not graph or not graph.get("nodes"):
        return graph
    center = graph["center"]
    cx, cy = center["x"], center["y"]
    cr = float(center.get("r", 0.0))
    nodes = graph["nodes"]
    width, height = graph["width"], graph["height"]

    # Снимок исходных позиций: дома для пружины, дельты подписей и сопоставление концов рёбер.
    homes = [(n["x"], n["y"]) for n in nodes]
    ldelta = [(n["lx"] - n["x"], n["ly"] - n["y"]) for n in nodes]
    maxr = max((n["r"] for n in nodes), default=1.0) or 1.0
    by_coord: dict[tuple[float, float], int] = {}
    for k, hp in enumerate(homes):
        by_coord.setdefault(hp, k)

    def _ref(x: float, y: float):
        if abs(x - cx) < 1e-6 and abs(y - cy) < 1e-6:
            return ("c", None)
        k = by_coord.get((x, y))
        return ("n", k) if k is not None else ("f", (x, y))

    edge_refs = [(_ref(e["x1"], e["y1"]), _ref(e["x2"], e["y2"])) for e in graph["edges"]]

    for _ in range(iters):
        # Попарное отталкивание при пересечении (вес смещения — по «массе» соседа).
        for i in range(len(nodes)):
            ni = nodes[i]
            for j in range(i + 1, len(nodes)):
                nj = nodes[j]
                dx = nj["x"] - ni["x"]
                dy = nj["y"] - ni["y"]
                d = math.hypot(dx, dy) or 1e-6
                mind = ni["r"] + nj["r"] + pad
                if d < mind:
                    ov = mind - d
                    ux, uy = dx / d, dy / d
                    mi, mj = ni["r"], nj["r"]
                    tot = mi + mj or 1.0
                    ni["x"] -= ux * ov * (mj / tot)
                    ni["y"] -= uy * ov * (mj / tot)
                    nj["x"] += ux * ov * (mi / tot)
                    nj["y"] += uy * ov * (mi / tot)
        # Отталкивание от хаба + АНИЗОТРОПНАЯ пружина: тангенциально (угол) держим крепко —
        # сохраняем порядок узлов по кольцу; радиально — слабо, чтобы переполненное кольцо могло
        # «вспухнуть» наружу и точки разошлись (иначе на фикс-радиусе им просто не хватает места).
        for k, n in enumerate(nodes):
            dx = n["x"] - cx
            dy = n["y"] - cy
            d = math.hypot(dx, dy) or 1e-6
            mind = cr + n["r"] + pad
            if d < mind:
                ux, uy = dx / d, dy / d
                n["x"] = cx + ux * mind
                n["y"] = cy + uy * mind
                dx, dy = n["x"] - cx, n["y"] - cy
                d = mind
            hx, hy = homes[k]
            hr = math.hypot(hx - cx, hy - cy) or 1e-6
            ha = math.atan2(hy - cy, hx - cx)
            mass = 1 + n["r"] / maxr
            # Тангенциально: вернуть на «домашний» луч (тот же радиус, угол → домашний).
            st = min(spring * 2.2 * mass, 0.9)
            tx = cx + d * math.cos(ha)
            ty = cy + d * math.sin(ha)
            n["x"] += (tx - n["x"]) * st
            n["y"] += (ty - n["y"]) * st
            # Радиально: мягко тянем к домашнему радиусу (узел не должен улетать совсем, но и
            # вспухать наружу при тесноте — можно). Слабый коэффициент.
            ca = math.atan2(n["y"] - cy, n["x"] - cx)
            cr_now = math.hypot(n["x"] - cx, n["y"] - cy)
            sr = min(spring * 0.35 * mass, 0.5)
            rad = cr_now + (hr - cr_now) * sr
            n["x"] = cx + rad * math.cos(ca)
            n["y"] = cy + rad * math.sin(ca)

    # Кламп в холст, округление, перенос подписей жёстким сдвигом.
    for k, n in enumerate(nodes):
        n["x"] = round(min(max(n["x"], n["r"]), width - n["r"]), 1)
        n["y"] = round(min(max(n["y"], n["r"]), height - n["r"]), 1)
        ldx, ldy = ldelta[k]
        n["lx"] = round(n["x"] + ldx, 1)
        n["ly"] = round(n["y"] + ldy, 1)

    def _coord(r):
        kind, val = r
        if kind == "c":
            return cx, cy
        if kind == "n":
            return nodes[val]["x"], nodes[val]["y"]
        return val

    for e, (r1, r2) in zip(graph["edges"], edge_refs, strict=True):
        x1, y1 = _coord(r1)
        x2, y2 = _coord(r2)
        e["x1"], e["y1"] = round(x1, 1), round(y1, 1)
        e["x2"], e["y2"] = round(x2, 1), round(y2, 1)
    return graph
