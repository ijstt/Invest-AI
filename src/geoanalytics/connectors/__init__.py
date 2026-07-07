"""Пакет коннекторов.

Импорт модулей источников здесь регистрирует их в реестре (декоратор @register),
поэтому достаточно `import geoanalytics.connectors`, чтобы все источники стали видны.
"""

from geoanalytics.connectors import (  # noqa: F401  (регистрация)
    cbr,
    commodities,
    ecb,
    fred,
    interfax,
    kommersant,
    moex,
    rbc,
    telegram,
    telegram_mtproto,
    vedomosti,
)
from geoanalytics.connectors.registry import (
    all_connectors,
    available,
    get_connector,
)

__all__ = ["all_connectors", "available", "get_connector"]
