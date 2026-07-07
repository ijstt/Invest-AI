"""G7 (Волна 4): распространение влияния по графу знаний эмитентов.

Событие, ударившее по компании X, отзывается у связанных компаний: поставщик/
потребитель со-движутся при шоке спроса (цепочка поставок), конкуренты — слабее
(со-движение сектора). Берём готовые оценки EventImpact (событие→актив,
direction/magnitude) у СОСЕДЕЙ актива по графу и аттенюируем их по типу ребра и весу.

v1 — затухание на 1 шаг, направление сохраняется (упрощение: и поставщик, и
потребитель, и конкурент при рыночном шоке чаще со-движутся; идиосинкразия конкурента
«выиграл за счёт соперника» здесь не моделируется, поэтому конкурентам даём малый вес).

Чистое ядро `propagate_impact` — основной предмет тестов; DB-раннер
`graph_impacts_for_asset` собирает соседей и их события.
"""

from __future__ import annotations

from dataclasses import dataclass

# Базовое затухание вклада по типу ребра (в дополнение к весу ребра).
_PREDICATE_ATTENUATION = {
    "supplier_of": 0.6,     # цепочка поставок — сильная передача
    "competitor_of": 0.35,  # со-движение сектора — слабее
    "subsidiary_of": 0.55,  # холдинг (L2): новость дочки/материнской со-движет связку
}
# Ниже этого порога производный вклад считаем пренебрежимым (не показываем).
_MIN_MAGNITUDE = 0.05


def propagate_impact(direction: str, magnitude: float, predicate: str,
                     weight: float) -> tuple[str, float] | None:
    """(direction, magnitude) события соседа → производный вклад на актив.

    None — неизвестный предикат или вклад ниже порога значимости. Чистая функция.
    """
    att = _PREDICATE_ATTENUATION.get(predicate)
    if att is None:
        return None
    derived = round(magnitude * weight * att, 3)
    if derived < _MIN_MAGNITUDE:
        return None
    return direction, derived


@dataclass(frozen=True)
class GraphImpact:
    via_ticker: str        # сосед-источник события
    relation: str          # связь по-русски (поставщик/потребитель/конкурент)
    title: str             # заголовок события
    event_type: str | None
    direction: str
    magnitude: float
    url: str | None = None  # ссылка на новость-источник события соседа


# Метка связи с точки зрения АКТИВА: предикат + направление ребра.
def _relation_label(predicate: str, asset_is_subject: bool) -> str:
    if predicate == "competitor_of":
        return "конкурент"
    if predicate == "supplier_of":
        # supplier_of: subject поставляет объекту. Для объекта сосед — поставщик,
        # для субъекта сосед — потребитель.
        return "потребитель" if asset_is_subject else "поставщик"
    if predicate == "subsidiary_of":
        # subsidiary_of: subject (дочка) → object (материнская). Для дочки сосед —
        # материнская компания, для материнской сосед — дочерняя.
        return "материнская компания" if asset_is_subject else "дочерняя компания"
    return predicate


def _neighbors(session, asset_id: int) -> dict[int, tuple[str, str, float]]:
    """{сосед_id: (предикат, метка_связи, вес)} по рёбрам графа в обе стороны.

    competitor_of хранится одним направлением, supplier_of — направленно; берём и
    исходящие, и входящие рёбра актива."""
    from sqlalchemy import or_, select

    from geoanalytics.core.types import EntityType
    from geoanalytics.storage.models import Relation

    out: dict[int, tuple[str, str, float]] = {}
    rows = session.scalars(
        select(Relation).where(
            Relation.subject_type == EntityType.ASSET.value,
            Relation.object_type == EntityType.ASSET.value,
            Relation.predicate.in_(tuple(_PREDICATE_ATTENUATION)),
            or_(Relation.subject_id == asset_id, Relation.object_id == asset_id),
        )
    )
    for r in rows:
        asset_is_subject = r.subject_id == asset_id
        neighbor_id = r.object_id if asset_is_subject else r.subject_id
        label = _relation_label(r.predicate, asset_is_subject)
        # При дубле предикатов берём более сильное ребро (вес).
        prev = out.get(neighbor_id)
        if prev is None or r.weight > prev[2]:
            out[neighbor_id] = (r.predicate, label, r.weight)
    return out


def graph_impacts_for_asset(session, asset_id: int, *, hours: int = 168,
                            limit: int = 8) -> list[GraphImpact]:
    """Косвенные влияния на актив через граф: события соседей, аттенюированные.

    Отсортировано по производной значимости убыв. Пустой список — нет соседей или
    значимых событий у них.
    """
    from sqlalchemy import select

    from geoanalytics.context.events import top_impacts_for_asset
    from geoanalytics.storage.models import Asset

    neighbors = _neighbors(session, asset_id)
    if not neighbors:
        return []
    tickers = {a.id: a.ticker for a in session.scalars(
        select(Asset).where(Asset.id.in_(list(neighbors))))}

    # Дедуп по (сосед, заголовок): одна новость часто заходит несколькими статьями/
    # событиями — оставляем сильнейший вклад.
    best: dict[tuple[str, str], GraphImpact] = {}
    for nid, (predicate, label, weight) in neighbors.items():
        via = tickers.get(nid, str(nid))
        for ev in top_impacts_for_asset(session, nid, hours=hours, limit=3):
            prop = propagate_impact(ev["direction"], ev["magnitude"],
                                    predicate, weight)
            if prop is None:
                continue
            direction, magnitude = prop
            key = (via, ev["title"])
            prev = best.get(key)
            if prev is None or magnitude > prev.magnitude:
                best[key] = GraphImpact(
                    via_ticker=via, relation=label, title=ev["title"],
                    event_type=ev.get("type"), direction=direction,
                    magnitude=magnitude, url=ev.get("url"),
                )
    impacts = sorted(best.values(), key=lambda g: g.magnitude, reverse=True)
    return impacts[:limit]
