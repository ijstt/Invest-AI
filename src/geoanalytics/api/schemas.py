"""Pydantic-схемы ответов REST API (M5).

Зеркалят dataclass-результаты слоя `query`/`analytics`. Поля со свободной
структурой (индикаторы, факторы) оставлены как `dict` — это намеренно: их состав
зависит от наличия данных, а API лишь прозрачно отдаёт уже собранную аналитику.
"""

from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    sources: int


class SourceInfo(BaseModel):
    name: str
    kind: str


class AssetInfo(BaseModel):
    """Краткая карточка актива (для списка/автодополнения)."""

    ticker: str
    name: str
    sector: str | None = None


class NewsResponse(BaseModel):
    """Снимок рынка «что по новостям» (зеркало MarketSnapshot)."""

    key_rate: float | None = None
    key_rate_date: str | None = None
    fx: dict[str, float] = {}
    top_gainers: list[dict] = []
    top_losers: list[dict] = []
    headlines: list[dict] = []
    sentiment_breakdown: dict[str, int] = {}
    top_events: list[tuple[str, int]] = []
    llm_summary: str | None = None


class AssetResponse(BaseModel):
    """Аналитический отчёт по активу (зеркало AssetReport)."""

    ticker: str
    found: bool
    name: str | None = None
    sector: str | None = None
    indicators: dict = {}
    macro: dict = {}
    factors: dict = {}
    correlations: dict = {}
    events: list[dict] = []
    news: list[dict] = []
    # F5: последний дивиденд из новостей — {value, published_at, yield_pct|None}.
    dividend: dict | None = None
    narrative: str | None = None
    note: str = ""


class TradeSchema(BaseModel):
    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    ret_pct: float


class BacktestResponse(BaseModel):
    """Результат бэктеста (зеркало BacktestResult + тикер/стратегия)."""

    ticker: str
    strategy: str
    bars: int
    total_return_pct: float
    buy_hold_return_pct: float
    index_return_pct: float | None = None
    alpha_pct: float | None = None
    cagr_pct: float | None = None
    sharpe: float | None = None
    max_drawdown_pct: float
    hit_rate: float | None = None
    num_trades: int
    exposure: float
    equity_curve: list[float] = []
    trades: list[TradeSchema] = []


class EventImpactSchema(BaseModel):
    ticker: str
    direction: str
    magnitude: float


class EventResponse(BaseModel):
    event_type: str
    title: str
    occurred_at: str | None = None
    impacts: list[EventImpactSchema] = []


class AlertResponse(BaseModel):
    """Сработавший алерт (зеркало записи `alerts`)."""

    id: int | None = None
    alert_type: str
    ticker: str | None = None
    severity: str
    title: str
    message: str
    created_at: str | None = None
    acknowledged_at: str | None = None
    channels: list[str] = []
