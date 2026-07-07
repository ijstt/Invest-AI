"""Эмбеддинги для семантического поиска (RAG) и кластеризации новостей.

Используется FastEmbed (ONNX, CPU-friendly) с моделью multilingual-e5-large (1024 dim).
Загрузка ленивая и устойчивая: если пакет/модель недоступны, возвращаем None,
и конвейер просто пропускает шаг эмбеддинга (graceful degradation на слабом железе).
"""

from __future__ import annotations

from functools import lru_cache

from config.settings import get_settings
from geoanalytics.core.logging import get_logger
from geoanalytics.storage.models import EMBEDDING_DIM

log = get_logger("nlp.embeddings")


class Embedder:
    """Обёртка над FastEmbed. Создаётся лениво через get_embedder()."""

    def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
        from fastembed import TextEmbedding  # тяжёлый импорт — внутри конструктора

        self.model_name = model_name
        # Фактическая размерность модели; заполняется пробным эмбеддингом в
        # get_embedder (None — ещё не проверена).
        self.dim: int | None = None
        # cache_dir передаём явно (не через env конкретной службы): постоянный каталог
        # вместо эфемерного /tmp, иначе веса слетают и RAG молча отключается.
        kwargs = {"cache_dir": cache_dir} if cache_dir else {}
        self._model = TextEmbedding(model_name=model_name, **kwargs)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Возвращает векторы для списка текстов."""
        return [vec.tolist() for vec in self._model.embed(texts)]

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


def model_status() -> tuple[str, str]:
    """Статус эмбеддера для health-check (I4): ("ok"|"degraded", деталь).

    Без эмбеддера семантический поиск и RAG молча деградируют (Б17) — это degraded.
    Несовпадение размерности со схемой БД (Б16) — тоже degraded: иначе ошибка
    всплывёт только в рантайме при вставке в halfvec.
    """
    embedder = get_embedder()
    if embedder is None:
        return "degraded", "эмбеддер не загрузился — семантика/RAG отключены"
    if embedder.dim is not None and embedder.dim != EMBEDDING_DIM:
        return "degraded", (f"размерность модели {embedder.dim} ≠ схемы БД "
                            f"{EMBEDDING_DIM} (halfvec) — вставки будут падать; "
                            "нужна миграция или другая модель")
    return "ok", embedder.model_name


@lru_cache
def get_embedder() -> Embedder | None:
    """Singleton-эмбеддер. None, если FastEmbed/модель недоступны."""
    settings = get_settings()
    model_name = settings.embedding_model
    try:
        embedder = Embedder(model_name, cache_dir=settings.embedding_cache_dir)
        # Фактическая размерность — пробным эмбеддингом; расхождение со схемой
        # ловит health (Б16), здесь только громкий лог.
        embedder.dim = len(embedder.embed_one("проверка"))
        if embedder.dim != EMBEDDING_DIM:
            log.warning("embedding_dim_mismatch",
                        expected=EMBEDDING_DIM, got=embedder.dim)
        log.info("embedder_ready", model=model_name, dim=embedder.dim)
        return embedder
    except Exception as exc:  # noqa: BLE001 — модель опциональна
        log.warning("embedder_unavailable", model=model_name, error=str(exc))
        return None
