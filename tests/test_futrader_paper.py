"""Трек 2 / Фаза D: тесты гейта качества бумажной торговли (чистая логика допуска)."""

from __future__ import annotations

from dataclasses import dataclass

from geoanalytics.futrader.paper import QualityGate, passes_gate, regime_blocks_entry


@dataclass
class _Champ:
    lift: float | None = 0.05
    sharpe: float | None = 0.1
    n_taken: int = 50
    n_samples: int = 300


@dataclass
class _Regime:
    label: str


class TestRegimeBlocksEntry:
    def test_none_regime_allows(self):
        assert regime_blocks_entry(None) is False

    def test_calm_regime_allows(self):
        assert regime_blocks_entry(_Regime("спокойный")) is False
        assert regime_blocks_entry(_Regime("повышенный")) is False

    def test_crisis_regime_blocks(self):
        assert regime_blocks_entry(_Regime("кризис")) is True

    def test_custom_blocked_set(self):
        assert regime_blocks_entry(_Regime("повышенный"), ("повышенный", "кризис")) is True
        assert regime_blocks_entry(_Regime("спокойный"), ("повышенный", "кризис")) is False


class TestPassesGate:
    def test_none_champion_rejected(self):
        assert passes_gate(None, QualityGate()) is False

    def test_good_champion_passes(self):
        assert passes_gate(_Champ(), QualityGate()) is True

    def test_negative_lift_rejected(self):
        assert passes_gate(_Champ(lift=-0.01), QualityGate()) is False

    def test_zero_lift_rejected(self):
        assert passes_gate(_Champ(lift=0.0), QualityGate(min_lift=0.0)) is False

    def test_negative_sharpe_rejected(self):
        assert passes_gate(_Champ(sharpe=-0.05), QualityGate()) is False

    def test_missing_sharpe_rejected(self):
        assert passes_gate(_Champ(sharpe=None), QualityGate()) is False

    def test_too_few_taken_rejected(self):
        assert passes_gate(_Champ(n_taken=5), QualityGate(min_taken=20)) is False

    def test_too_few_samples_rejected(self):
        assert passes_gate(_Champ(n_samples=50), QualityGate(min_samples=120)) is False

    def test_custom_thresholds(self):
        champ = _Champ(lift=0.02, sharpe=0.05, n_taken=30, n_samples=200)
        assert passes_gate(champ, QualityGate(min_lift=0.03)) is False   # ужесточили lift
        assert passes_gate(champ, QualityGate(min_lift=0.01)) is True


# --- Сброс бумажного счёта (reset_account): scoped delete, датасет не трогаем ---
class _FakeResult:
    def __init__(self, n: int) -> None:
        self.rowcount = n


class _FakeSession:
    """Фейк-сессия: ловит DELETE-стейтменты и отдаёт заданные rowcount по порядку."""

    def __init__(self, counts: list[int]) -> None:
        self._counts = list(counts)
        self.deleted_tables: list[str] = []

    def execute(self, stmt):
        self.deleted_tables.append(stmt.table.name)
        return _FakeResult(self._counts.pop(0))


def test_reset_account_clears_paper_tables_and_risk_state():
    from geoanalytics.storage.repositories import FuturesPaperRepository

    sess = _FakeSession([5, 4, 47, 1])
    out = FuturesPaperRepository(sess).reset_account("demo")
    assert out == {"positions": 5, "equity": 4, "trades": 47}
    # удаляет три таблицы счёта + сбрасывает futures_risk_state — обучающий датасет не трогает
    assert set(sess.deleted_tables) == {
        "futures_paper_positions", "futures_paper_equity", "futures_paper_trades", "futures_risk_state"}
