"""Базовый интерфейс источника данных и контейнер сырого документа."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field

from geoanalytics.core.types import SourceKind


@dataclass(slots=True)
class RawItem:
    """Единица данных, полученная от источника, до записи в БД.

    Универсальна для новостей и рыночных данных: текст обязателен (для дедупа),
    структурированные поля кладутся в `payload`.
    """

    source: str
    raw_text: str
    external_id: str | None = None
    payload: dict = field(default_factory=dict)


class BaseConnector(ABC):
    """Абстрактный коннектор источника.

    Реализации обязаны задать `name` и `kind` и реализовать `fetch()`.
    Сетевые вызовы должны быть устойчивы к сбоям (ретраи на уровне реализации).
    """

    name: str
    kind: SourceKind

    @abstractmethod
    def fetch(self) -> Iterable[RawItem]:
        """Забирает свежие данные источника и возвращает их как поток RawItem."""
        raise NotImplementedError
