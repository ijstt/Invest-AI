"""Трек 2 / Пул 4: веса уникальности выборки (López de Prado).

Triple-barrier метки ПЕРЕКРЫВАЮТСЯ во времени: решения в близких барах смотрят на пересекающиеся
будущие окна → их исходы скоррелированы. Без поправки модель «засчитывает» перекрытые примеры
многократно → переобучение и завышенная уверенность. Вес уникальности решения = средняя по
бар-сетке доля 1/concurrency на интервале жизни метки [вход, исход]; нормируем к среднему 1, чтобы
сохранить эффективный размер выборки.

Concurrency считается ПО ИНСТРУМЕНТУ (метки на разных активах не пересекаются в одном ряду).
Чистая функция (без БД): на вход — (asset_code, ts, outcome_ts) на каждое решение.
"""

from __future__ import annotations

from datetime import datetime


def uniqueness_weights(
        spans: list[tuple[str, datetime, datetime | None]]) -> list[float]:
    """Веса уникальности решений. `spans[i]=(asset, ts, outcome_ts)`. Нормированы к среднему 1.

    Неразмеченные (outcome_ts=None) получают вес 1 и в нормировке не участвуют. Сетка concurrency
    для актива — множество ts его решений; уникальность_i = среднее 1/c(g) по точкам g сетки внутри
    [ts_i, outcome_ts_i], где c(g) — сколько меток актива активны в g.
    """
    n = len(spans)
    weights = [1.0] * n
    by_asset: dict[str, list[int]] = {}
    for i, (asset, _ts, _out) in enumerate(spans):
        by_asset.setdefault(asset, []).append(i)

    for idxs in by_asset.values():
        labeled = [i for i in idxs if spans[i][2] is not None]
        if not labeled:
            continue
        grid = sorted({spans[i][1] for i in idxs})
        conc: dict[datetime, int] = {}
        for g in grid:
            c = sum(1 for i in labeled if spans[i][1] <= g <= spans[i][2])
            conc[g] = max(c, 1)
        for i in labeled:
            ts_i, out_i = spans[i][1], spans[i][2]
            pts = [g for g in grid if ts_i <= g <= out_i]
            if pts:
                weights[i] = sum(1.0 / conc[g] for g in pts) / len(pts)

    labeled_w = [weights[i] for i in range(n) if spans[i][2] is not None]
    if labeled_w:
        mean = sum(labeled_w) / len(labeled_w)
        if mean > 0:                          # нормируем ТОЛЬКО размеченные; неразмеченные = 1.0
            for i in range(n):
                if spans[i][2] is not None:
                    weights[i] /= mean
    return weights
