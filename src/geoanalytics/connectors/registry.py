"""Реестр коннекторов: единая точка регистрации и поиска источников.

Новый источник достаточно зарегистрировать через декоратор @register —
CLI и оркестратор подхватят его автоматически.
"""

from __future__ import annotations

from geoanalytics.connectors.base import BaseConnector

_REGISTRY: dict[str, type[BaseConnector]] = {}


def register(cls: type[BaseConnector]) -> type[BaseConnector]:
    """Декоратор регистрации класса коннектора по его `name`."""
    if not getattr(cls, "name", None):
        raise ValueError(f"Коннектор {cls.__name__} должен задать атрибут name")
    _REGISTRY[cls.name] = cls
    return cls


def get_connector(name: str) -> BaseConnector:
    """Создаёт экземпляр коннектора по имени."""
    if name not in _REGISTRY:
        raise KeyError(f"Неизвестный источник: {name}. Доступно: {available()}")
    return _REGISTRY[name]()


def available() -> list[str]:
    """Список имён зарегистрированных источников."""
    return sorted(_REGISTRY)


def all_connectors() -> list[BaseConnector]:
    """Экземпляры всех зарегистрированных коннекторов."""
    return [cls() for cls in _REGISTRY.values()]
