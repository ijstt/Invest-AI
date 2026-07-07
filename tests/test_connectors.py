"""Тесты реестра коннекторов и базовых утилит (без БД и сети)."""

from __future__ import annotations

from geoanalytics.connectors import all_connectors, available, get_connector
from geoanalytics.connectors.base import BaseConnector


def test_all_sources_registered():
    """Стартовые источники и сырьевые макро-факторы зарегистрированы.

    Металлы (gold/silver/platinum/palladium) с 2026-06-13 идут внутри cbr
    (учётные цены, ₽/г) — отдельных FORTS-коннекторов больше нет."""
    assert set(available()) >= {"interfax", "moex", "cbr", "brent", "telegram"}
    assert "gold" not in available() and "silver" not in available()


def test_commodity_candidate_contracts_by_prefix():
    """Кандидаты фронт-месяца строятся по коду серии (BR)."""
    from datetime import date

    from geoanalytics.connectors.commodities import _candidate_contracts

    brent = _candidate_contracts("BR", date(2026, 6, 1))
    assert brent[0] == "BRM6"           # июнь 2026 → буква M, год 6
    assert all(c.startswith("BR") for c in brent)
    assert _candidate_contracts("BR", date(2026, 1, 1))[0] == "BRF6"


def test_get_connector_returns_instance():
    conn = get_connector("cbr")
    assert isinstance(conn, BaseConnector)
    assert conn.name == "cbr"


def test_get_unknown_source_raises():
    import pytest

    with pytest.raises(KeyError):
        get_connector("does-not-exist")


def test_every_connector_has_name_and_kind():
    for conn in all_connectors():
        assert conn.name
        assert conn.kind


def test_cbr_fetch_metals_yields_rawitems(monkeypatch):
    """Металлы ЦБ: окно последних дней → RawItem'ы macro с unit RUB/g."""
    from unittest.mock import MagicMock

    from geoanalytics.connectors import cbr

    xml = (
        '<?xml version="1.0" encoding="windows-1251"?>'
        '<Metall><Record Date="11.06.2026" Code="1">'
        "<Buy>10000,5</Buy><Sell>10000,5</Sell></Record>"
        '<Record Date="11.06.2026" Code="4">'
        "<Buy>3000,1</Buy><Sell>3000,1</Sell></Record></Metall>"
    ).encode("cp1251")
    resp = MagicMock(content=xml)
    monkeypatch.setattr(cbr, "_get", lambda url, params=None: resp)

    items = list(cbr.CbrConnector()._fetch_metals())
    assert [i.payload["indicator"] for i in items] == ["gold", "palladium"]
    it = items[0]
    assert it.source == "cbr"
    assert it.external_id == "gold:11.06.2026"
    assert it.payload["value"] == 10000.5
    assert it.payload["unit"] == "RUB/g"
    assert it.payload["date"] == "11.06.2026"


def test_cbr_fetch_metals_survives_network_error(monkeypatch):
    """Сбой металлов не валит коннектор (валюты/ставка независимы)."""
    from geoanalytics.connectors import cbr

    def _boom(url, params=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(cbr, "_get", _boom)
    assert list(cbr.CbrConnector()._fetch_metals()) == []
