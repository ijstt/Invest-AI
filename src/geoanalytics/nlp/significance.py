"""Оценка значимости новости — единый чистый модуль (M6).

Значимость в [0,1] — фундамент сразу для нескольких слоёв:
- фильтр на инжесте (базовые новости не сохраняем),
- TTL-ретеншн (срок хранения растёт со значимостью),
- gate алертов (шлём только значимое),
- (позже, M6.5) слабые метки для дообучения классификатора значимости.

Формула прозрачная и детерминированная:
    significance = w_type · type_weight + w_sent · |sentiment| + w_link · link_factor
Все три слагаемых в [0,1], веса по умолчанию суммируются в 1.0 → результат в [0,1].
В M6.5 эту функцию можно заменить/скорректировать обученной моделью, сохранив интерфейс.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from config.settings import get_settings
from geoanalytics.core.logging import get_logger
from geoanalytics.core.types import EventType

log = get_logger("nlp.significance")

# Значимость типа события (0..1). Раньше жила в context/events.py как _EVENT_WEIGHT —
# теперь единый источник правды здесь (переиспользуется и в оценке влияния события).
EVENT_WEIGHT: dict[str, float] = {
    EventType.SANCTIONS.value: 1.0,
    EventType.EARNINGS.value: 0.85,
    EventType.MERGER.value: 0.8,
    EventType.DIVIDENDS.value: 0.7,
    EventType.GEOPOLITICS.value: 0.7,
    EventType.REGULATION.value: 0.6,
    EventType.MACRO.value: 0.6,
    # OTHER понижен 0.3→0.1: «прочее» само по себе почти не значимо (значимость должна
    # идти от тональности/связей, а не от факта наличия категории) — меньше мусора в алертах.
    EventType.OTHER.value: 0.1,
    # NOISE (спорт/ДТП/культура) — практически нулевой вес: такие новости не нужны в алертах.
    EventType.NOISE.value: 0.02,
}

# Веса слагаемых по умолчанию (переопределяются настройками GEO_SIG_W_*).
DEFAULT_W_TYPE = 0.5
DEFAULT_W_SENT = 0.3
DEFAULT_W_LINK = 0.2


def type_weight(event_type: str | None) -> float:
    """Вес категории события (0..1). Неизвестная/пустая категория → как OTHER."""
    return EVENT_WEIGHT.get(event_type or "", EVENT_WEIGHT[EventType.OTHER.value])


def significance_score(
    event_type: str | None,
    sentiment_score: float | None,
    link_relevances: Iterable[float] | None = None,
    *,
    w_type: float = DEFAULT_W_TYPE,
    w_sent: float = DEFAULT_W_SENT,
    w_link: float = DEFAULT_W_LINK,
) -> float:
    """Значимость новости в [0,1]. Чистая функция (без БД) — основной предмет тестов.

    `link_relevances` — релевантности связанных активов (сумма насыщается до 1.0:
    одна уверенная привязка уже даёт полный вклад фактора связей).
    """
    tw = type_weight(event_type)
    sent = min(1.0, abs(sentiment_score)) if sentiment_score is not None else 0.0
    link = min(1.0, sum(link_relevances or ()))
    raw = w_type * tw + w_sent * sent + w_link * link
    return round(max(0.0, min(1.0, raw)), 3)


# --------------------------------------------------------------------------- #
# Бакеты значимости — для слабых меток дообучения и для маппинга предсказаний модели.
# --------------------------------------------------------------------------- #
SIG_BUCKETS = ("low", "medium", "high")
# Представительное значение бакета (для обратного маппинга метки модели в [0,1]).
# Поддерживаются обе схемы меток: LLM-учитель (low/medium/high) и рыночное золото
# significance v3 (E3: flat/moved — двинула ли новость цену; гейт алертов 0.6
# пропускает только moved). Деплой v3 — через GEO_SIGNIFICANCE_ADAPTER_PATH,
# когда улучшение доказано на рыночном eval (scripts/eval_market_significance.py).
_BUCKET_VALUE = {"low": 0.15, "medium": 0.5, "high": 0.85,
                 "flat": 0.15, "moved": 0.85}


def significance_bucket(value: float, low: float = 0.34, high: float = 0.66) -> str:
    """Дискретизирует значимость в low/medium/high (для меток датасета)."""
    if value < low:
        return "low"
    if value >= high:
        return "high"
    return "medium"


def significance_gates(settings=None) -> dict[str, float]:
    """Б6: единый снимок каскада порогов значимости — инжест-фильтр → алерт-гейт.

    Источник правды — settings (`GEO_MIN_SIGNIFICANCE` / `GEO_ALERT_MIN_SIGNIFICANCE`).
    Собрано в одном месте, чтобы пороги не «разъезжались» независимо без видимости каскада
    (исторически их крутили руками без A/B). Ретеншн масштабируется значимостью непрерывно
    (`retention_min/max_days`) — это не бинарный гейт, поэтому в каскад не входит.

    Инжест-фильтр (0.2) применяется ТОЛЬКО вместе с «нет связей И тип OTHER/NOISE» — то есть
    рубит истинный шум, а не весь low-бакет; значимый low со связями/типом проходит.
    """
    s = settings or get_settings()
    return {"ingest": s.min_significance, "alert": s.alert_min_significance}


def validate_cascade(settings=None) -> list[str]:
    """Б6: инвариант каскада значимости. Пустой список — каскад согласован.

    - оба порога в [0, 1];
    - монотонность: инжест-фильтр ≤ алерт-гейт (иначе на инжесте отбрасывается то, что
      прошло бы алерт-гейт → алерты тихо недосчитывают);
    - при активной дискретной модели (бакеты 0.15/0.5/0.85) алерт-гейт должен лежать в
      [low, high]: выше high → алерты не сработают НИКОГДА (тишина); ниже low → гейт не
      фильтрует (всё проходит). Сам выбор, какие бакеты пропускать (напр. 0.6 = только high),
      — это дизайн, не ошибка.
    """
    g = significance_gates(settings)
    ingest, alert = g["ingest"], g["alert"]
    problems: list[str] = []
    if not 0.0 <= ingest <= 1.0:
        problems.append(f"инжест-порог вне [0,1]: {ingest}")
    if not 0.0 <= alert <= 1.0:
        problems.append(f"алерт-гейт вне [0,1]: {alert}")
    if ingest > alert:
        problems.append(
            f"инжест-порог {ingest} > алерт-гейт {alert}: алерты недосчитывают "
            "(на инжесте отбрасывается то, что прошло бы гейт)")
    if _get_model() is not None:
        low, high = _BUCKET_VALUE["low"], _BUCKET_VALUE["high"]
        if alert > high:
            problems.append(
                f"алерт-гейт {alert} > высшего бакета {high}: при дискретной модели "
                "алерты не сработают НИКОГДА")
        elif alert < low:
            problems.append(
                f"алерт-гейт {alert} < низшего бакета {low}: гейт не фильтрует "
                "(всё проходит)")
    return problems


@lru_cache
def _get_model():
    """Дообученный классификатор значимости (M6.5), если задан и грузится; иначе None.

    Б1 (Волна 1): фолбэк модель→формула НЕ тихий. Модель дискретна (0.15/0.5/0.85),
    формула непрерывна — gate алертов 0.6 откалиброван под модель, и молчаливая подмена
    сдвигает распределение (шторм или тишина алертов). Поэтому сбой загрузки при
    настроенном пути — это ERROR, а health-check (geo health / scheduler) поднимает алерт.
    """
    path = get_settings().significance_adapter_path
    if not path:
        return None
    if not Path(path).exists():
        log.error("significance_adapter_missing_FORMULA_FALLBACK", path=path)
        return None
    try:
        from geoanalytics.nlp._seqcls import SeqClsAdapter

        model = SeqClsAdapter(path)
        log.info("significance_model_ready", path=path)
        return model
    except Exception as exc:  # noqa: BLE001 — конвейер выживает на формуле, но громко (Б1)
        log.error("significance_model_failed_FORMULA_FALLBACK", error=str(exc))
        return None


def model_status() -> tuple[str, str]:
    """Статус значимости для health-check (I4): ("ok"|"degraded", деталь).

    degraded — адаптер настроен, но не загрузился (активна формула: распределение
    значимостей другое, гейт алертов 0.6 не откалиброван под неё — Б1).
    """
    configured = bool(get_settings().significance_adapter_path)
    if _get_model() is not None:
        return "ok", "модель (дискретные бакеты)"
    if configured:
        return "degraded", "адаптер настроен, но не загрузился — активна ФОРМУЛА (Б1)"
    return "ok", "формула (адаптер не настроен)"


def predict_significance(text: str) -> float | None:
    """Значимость текста по дообученной модели (бакет → значение). None — модели нет.

    Используется как опциональный override формулы `significance_score` в конвейере.
    """
    model = _get_model()
    if model is None:
        return None
    try:
        return _BUCKET_VALUE.get(model.predict_label(text), 0.5)
    except Exception as exc:  # noqa: BLE001 — деградация до формулы, но громко (Б1)
        log.error("significance_predict_failed_FORMULA_FALLBACK", error=str(exc))
        return None
