"""Тесты L5: цветовая полоса истории режимов рынка (чистый хелпер дашборда)."""

from __future__ import annotations

from datetime import date

from geoanalytics.api.web import _regime_strip


class _Row:
    def __init__(self, day, state, label, vol=1.0):
        self.day = day
        self.state = state
        self.label = label
        self.vol = vol


def test_regime_strip_color_mapping_three_states():
    rows = [
        _Row(date(2024, 1, 1), 0, "спокойный"),
        _Row(date(2024, 1, 2), 1, "переходный"),
        _Row(date(2024, 1, 3), 2, "кризис", vol=2.5),
    ]
    strip = _regime_strip(rows, width=300, height=12)
    assert [c["cls"] for c in strip["cells"]] == ["up", "flat", "down"]
    assert strip["current"] == "кризис"
    assert strip["vol"] == 2.5
    assert strip["first_day"] == date(2024, 1, 1)
    assert strip["day"] == date(2024, 1, 3)
    # ячейки покрывают ширину слева направо
    assert strip["cells"][0]["x"] == 0.0


def test_regime_strip_two_states():
    # при 2 режимах (0/1) промежуточного «flat» нет — только спокойный/кризис
    rows = [_Row(date(2024, 1, 1), 0, "спокойный"), _Row(date(2024, 1, 2), 1, "кризис")]
    strip = _regime_strip(rows)
    assert [c["cls"] for c in strip["cells"]] == ["up", "down"]


def test_regime_strip_empty():
    assert _regime_strip([]) is None
