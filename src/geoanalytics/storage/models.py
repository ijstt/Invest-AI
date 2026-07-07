"""Модели данных (SQLAlchemy 2.0).

Слои таблиц:
- raw       — сырые данные источников (append-only): raw_documents, raw_market;
- entities  — сущности графа знаний: assets, companies, sectors, countries, people, events;
- news      — новости и их связи: articles, article_entities, embeddings;
- timeseries— тайм-серии (Timescale hypertables): prices, macro_series, fx_rates;
- analysis  — производная аналитика: asset_context, sentiment_scores, event_impacts;
- graph     — связи между сущностями: relations.

Тайм-серии превращаются в hypertable'ы отдельной миграцией (Alembic), а не здесь.
"""

from __future__ import annotations

from datetime import date, datetime

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Размерность эмбеддингов (intfloat/multilingual-e5-large → 1024). Менять синхронно с моделью.
# Тип колонки — halfvec (fp16): вдвое легче по диску/памяти, чем vector (fp32), без
# заметной потери recall для e5. См. миграцию 0007.
EMBEDDING_DIM = 1024


class Base(DeclarativeBase):
    """Базовый класс всех моделей."""


# --------------------------------------------------------------------------- #
# RAW-слой (append-only): хранит данные ровно как получили от источника.
# --------------------------------------------------------------------------- #
class RawDocument(Base):
    """Сырой документ от источника (новость, страница) до обработки."""

    __tablename__ = "raw_documents"
    __table_args__ = (UniqueConstraint("source", "content_hash", name="uq_raw_doc_hash"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), index=True)       # interfax, moex...
    external_id: Mapped[str | None] = mapped_column(String(256))      # id/url на стороне источника
    content_hash: Mapped[str] = mapped_column(String(64))            # для дедупликации
    raw_text: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed: Mapped[bool] = mapped_column(default=False, index=True)


# --------------------------------------------------------------------------- #
# Сущности графа знаний.
# --------------------------------------------------------------------------- #
class Sector(Base):
    __tablename__ = "sectors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)


class Country(Base):
    __tablename__ = "countries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(3), unique=True)         # ISO (RUS, USA...)
    name: Mapped[str] = mapped_column(String(128))


class MacroTheme(Base):
    """Макро-тема для тематической привязки новостей (санкции, инфляция, ставка, курс…)."""

    __tablename__ = "macro_themes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    keywords: Mapped[list | None] = mapped_column(JSON)              # маркеры для классификации


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    inn: Mapped[str | None] = mapped_column(String(12), index=True)
    sector_id: Mapped[int | None] = mapped_column(ForeignKey("sectors.id"))
    country_id: Mapped[int | None] = mapped_column(ForeignKey("countries.id"))
    # Алиасы для entity-linking (как компанию называют в новостях).
    aliases: Mapped[list | None] = mapped_column(JSON)
    # L2 (состав компании, миграция 0027): медленно-меняющийся профиль эмитента —
    # снапшот последних известных значений (заполняется из smart-lab/вручную).
    description: Mapped[str | None] = mapped_column(Text)        # бизнес-описание
    market_cap: Mapped[float | None] = mapped_column(Float)      # капитализация, ₽
    free_float: Mapped[float | None] = mapped_column(Float)      # free-float, %
    shares: Mapped[float | None] = mapped_column(Float)          # число акций

    sector: Mapped[Sector | None] = relationship()
    assets: Mapped[list[Asset]] = relationship(back_populates="company")
    segments: Mapped[list[RevenueSegment]] = relationship(
        back_populates="company", cascade="all, delete-orphan")


class Asset(Base):
    """Торгуемый инструмент (акция/облигация/индекс)."""

    __tablename__ = "assets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # SBER, GAZP...
    isin: Mapped[str | None] = mapped_column(String(12), index=True)
    name: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(32), default="share")           # share/bond/index
    board: Mapped[str | None] = mapped_column(String(16))                     # TQBR и т.п.
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))

    company: Mapped[Company | None] = relationship(back_populates="assets")


class PortfolioPosition(Base):
    """Позиция виртуального портфеля (J1, Волна 4; per-user — 5c).

    `user_id` NULL — портфель владельца (дашборд/CLI); иначе личный портфель бот-пользователя.
    Одна строка на актив В РАМКАХ портфеля (уникальный индекс `uq_portfolio_user_asset` по
    COALESCE(user_id,0), asset_id — задаётся миграцией). avg_price опциональна: без неё позиция
    входит в стоимость/риск, но P&L не считается.
    """

    __tablename__ = "portfolio_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")        # NULL = портфель владельца
    )
    quantity: Mapped[float] = mapped_column(Float)
    avg_price: Mapped[float | None] = mapped_column(Numeric(18, 6))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    asset: Mapped[Asset] = relationship()


class CashBalance(Base):
    """Денежный/валютный баланс портфеля (расширение состава, не торгуемый актив).

    `user_id` NULL — баланс владельца (дашборд/CLI), иначе личный (бот-юзер) — как 5c.
    Уникальность по (COALESCE(user_id,0), currency) задаётся миграцией 0022. Оценивается в ₽:
    RUB = amount·1, прочие — по последнему курсу ЦБ из `fx_rates`. В риск входит нулевой
    доходностью (снижает волатильность/VaR портфеля).
    """

    __tablename__ = "cash_balances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")        # NULL = баланс владельца
    )
    currency: Mapped[str] = mapped_column(String(8))      # RUB/USD/EUR/CNY
    amount: Mapped[float] = mapped_column(Numeric(18, 2))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PortfolioSnapshot(Base):
    """Дневной снимок стоимости портфеля (хвост качественного просмотра, миграция 0023).

    Реальная история стоимости во времени вместо реконструкции по текущему составу. Одна строка
    на (портфель, дату): `total_value_rub` — рыночная оценка, `cost_basis_rub` — база покупки
    (Σ avg_price·qty по позициям с известной ценой) для P&L во времени (value − cost).
    `user_id` NULL — портфель владельца, иначе личный (как 5c/0022). Уникальность по
    (COALESCE(user_id,0), snapshot_date) — в миграции.
    """

    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")        # NULL = портфель владельца
    )
    snapshot_date: Mapped[date] = mapped_column(Date)
    total_value_rub: Mapped[float] = mapped_column(Numeric(18, 2))
    cost_basis_rub: Mapped[float | None] = mapped_column(Numeric(18, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EvalRun(Base):
    """Прогон непрерывной оценки модели (ось I/I2, миграция 0024): метрика качества на дату.

    Append-only ряд: накопление позволяет ловить дрейф качества во времени (напр. precision
    гейта значимости против фактической реакции рынка из `news_outcomes`) и алертить при
    деградации относительно трейлинг-базы.
    """

    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_name: Mapped[str] = mapped_column(String(64))    # significance/sentiment/events
    metric_name: Mapped[str] = mapped_column(String(48))   # market_precision/market_recall/…
    value: Mapped[float] = mapped_column(Float)
    n_samples: Mapped[int] = mapped_column(Integer)
    window_days: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MarketSentiment(Base):
    """Дневной индекс настроения по области (B1, миграция 0026): рынок / сектор / актив.

    Материализует агрегат сентимента во времени (раньше считался на лету и не хранился). Один
    ряд на (день, область, актив|сектор). Даёт тренд (`sent_ewma`), ширину (`breadth` = доля
    позитив − негатив) и разброс мнений (`dispersion`), а в паре с ценой — дивергенцию. Вход в
    консенсус сводки и рекомендации. UPSERT по (day, scope, asset_id, sector) — миграция 0026.
    """

    __tablename__ = "market_sentiment"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    scope: Mapped[str] = mapped_column(String(8))          # market | sector | asset
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE")        # NULL для market/sector
    )
    sector: Mapped[str | None] = mapped_column(String(64))  # NULL для market/asset
    sent_mean: Mapped[float] = mapped_column(Float)
    sent_ewma: Mapped[float] = mapped_column(Float)
    breadth: Mapped[float] = mapped_column(Float)
    dispersion: Mapped[float] = mapped_column(Float)
    n_docs: Mapped[int] = mapped_column(Integer)
    pressure_sum: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AssetFundamental(Base):
    """Фундаментальная метрика эмитента из отчёта (H5, миграция 0025).

    Извлечена rule-based из PDF (`nlp.fundamentals`). Значение в базовой единице (RUB/коэффициент),
    масштаб свёрнут. Одна строка на (актив, метрика, период, источник) — повторный разбор
    идемпотентен. `snippet` — фрагмент исходника для проверяемости (как [[ArticleNumber]]).
    """

    __tablename__ = "asset_fundamentals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    metric: Mapped[str] = mapped_column(String(24))        # revenue/net_profit/ebitda/…
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(12))          # RUB/USD/EUR/CNY | ratio
    period: Mapped[str | None] = mapped_column(String(16))  # 2024 | 2024-H1 | …
    source: Mapped[str] = mapped_column(String(64), default="pdf")
    snippet: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FactorScore(Base):
    """Кросс-секционный факторный скор актива на дату (L3: «как факторы складываются»).

    Одна строка на (день, актив, фактор), фактор ∈ value/quality/growth/composite. `zscore` —
    стандартизация суб-метрик по вселенной акций за день (винзор ±3), `percentile` — ранг [0..100]
    внутри вселенной. Идемпотентно: день переписывается. Накапливается во времени → тренд
    факторных экспозиций (закрывает провал «картины во времени»).
    """

    __tablename__ = "factor_scores"
    __table_args__ = (Index("ix_factor_day_asset", "day", "asset_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    factor: Mapped[str] = mapped_column(String(16))        # value/quality/growth/composite
    zscore: Mapped[float] = mapped_column(Float)
    percentile: Mapped[float | None] = mapped_column(Float)   # ранг по вселенной, 0..100
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FuturesCandle(Base):
    """Интрадей-свеча фьючерса ПО КОНТРАКТУ (Трек 2 / T2.1, миграция 0030).

    Отдельная от `prices` таблица форка фьючерсного трейдера: хранит минутные/часовые свечи
    с идентичностью конкретного контракта (`contract_secid` + `expiry`), чтобы реконструировать
    склейку непрерывного контракта на роллах. Hypertable по `ts` (объём интрадея велик).
    `ts` — реальное время бара (НЕ схлопывается в полночь). `interval` ∈ 1m/10m/1h.
    """

    __tablename__ = "futures_candles"
    __table_args__ = (
        UniqueConstraint("contract_secid", "ts", "interval", name="uq_futcandle_point"),
        Index("ix_futcandles_code_int_ts", "asset_code", "interval", "ts"),
    )

    # Композитный PK (id, ts): Timescale требует партиционную колонку ts в PK (как у prices).
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    asset_code: Mapped[str] = mapped_column(String(16), index=True)   # BR/GOLD/Si (ISS, case-sens.)
    contract_secid: Mapped[str] = mapped_column(String(32))           # BRN6/SiM6…
    expiry: Mapped[date | None] = mapped_column(Date)                 # LASTTRADEDATE контракта
    interval: Mapped[str] = mapped_column(String(8))                  # 1m/10m/1h
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FuturesOrderbook(Base):
    """Снимок стакана (L2 market depth) фьючерса по фронт-контракту (Трек 2, миграция 0037).

    ISS отдаёт лишь МГНОВЕННЫЙ снимок (истории нет) → копим ВПЕРЁД службой geo-depth. Сырьё
    (bids/asks JSON, top-N) + предрассчитанные скаляры микроструктуры (best/спред/объёмы/дисбаланс).
    Hypertable по `ts` (частые снимки). `ts` — UTC-метка момента снятия (как свечи MOEX)."""

    __tablename__ = "futures_orderbook"
    __table_args__ = (
        UniqueConstraint("contract_secid", "ts", name="uq_futorderbook_point"),
        Index("ix_futorderbook_code_ts", "asset_code", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    asset_code: Mapped[str] = mapped_column(String(16), index=True)   # BR/GOLD/Si
    contract_secid: Mapped[str] = mapped_column(String(32))           # фронт-контракт
    best_bid: Mapped[float | None] = mapped_column(Float)
    best_ask: Mapped[float | None] = mapped_column(Float)
    spread: Mapped[float | None] = mapped_column(Float)               # best_ask − best_bid
    bid_vol: Mapped[float | None] = mapped_column(Float)              # Σ объёма бидов по уровням
    ask_vol: Mapped[float | None] = mapped_column(Float)              # Σ объёма асков
    imbalance: Mapped[float | None] = mapped_column(Float)            # (bid−ask)/(bid+ask) ∈ [−1,1]
    levels: Mapped[int | None] = mapped_column(Integer)             # снято уровней с каждой стороны
    bids: Mapped[list | None] = mapped_column(JSON)                  # [[price, qty], …]
    asks: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FuturesDecision(Base):
    """Торговое решение фьючерсного форка + признаки + исход (Трек 2 / T2.3, миграция 0031).

    Один ряд на точку действия политики (вход/выход): что решено (`action`/`signed_qty`),
    при каком контексте (`features` на момент решения) и чем кончилось через `horizon_bars`
    (`outcome_*`, `label`). Накопительная обучающая выборка для T2.4 (fine-tune на своих исходах).
    `signed_qty` кодирует направленную ставку: buy +, sell/close − (т.е. «был ли разворот вниз
    верным»). Идемпотентно по (source, asset_code, interval, ts).
    """

    __tablename__ = "futures_decisions"
    __table_args__ = (
        UniqueConstraint("source", "asset_code", "interval", "ts", name="uq_futdecision_point"),
        Index("ix_futdecisions_code_int_ts", "asset_code", "interval", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))      # время решения = время бара
    asset_code: Mapped[str] = mapped_column(String(16), index=True)
    interval: Mapped[str] = mapped_column(String(8))                  # 1m/10m/1h
    contract_secid: Mapped[str | None] = mapped_column(String(32))    # фронт на момент решения
    source: Mapped[str] = mapped_column(String(32))                  # политика
    action: Mapped[str] = mapped_column(String(8))                  # buy/sell/hold/close
    signed_qty: Mapped[int] = mapped_column(Integer)                # знаковая ставка
    price: Mapped[float] = mapped_column(Float)                     # цена решения
    features: Mapped[dict | None] = mapped_column(JSON)             # контекст-признаки
    horizon_bars: Mapped[int | None] = mapped_column(Integer)
    outcome_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome_return_pct: Mapped[float | None] = mapped_column(Float)
    outcome_pnl_rub: Mapped[float | None] = mapped_column(Float)
    label: Mapped[str | None] = mapped_column(String(8))            # win/loss/flat
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FuturesModelRun(Base):
    """Реестр оценок политик форка (Трек 2 / Фаза B, миграция 0032).

    Один ряд на прогон walk-forward оценки: OOS-метрики обученной политики во времени (lift,
    Sharpe/Sortino/maxDD/profit-factor, deflated Sharpe). По реестру выбираем чемпиона и следим
    за улучшением по мере дозревания данных. Каждый прогон — новая строка-факт.
    """

    __tablename__ = "futures_model_runs"
    __table_args__ = (
        Index("ix_futmodelruns_key", "source", "asset_code", "interval", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source: Mapped[str] = mapped_column(String(32))
    asset_code: Mapped[str | None] = mapped_column(String(16))     # NULL = пулинг по всем активам
    interval: Mapped[str] = mapped_column(String(8))
    threshold: Mapped[float] = mapped_column(Float)
    n_folds: Mapped[int] = mapped_column(Integer)
    n_samples: Mapped[int] = mapped_column(Integer)
    n_taken: Mapped[int] = mapped_column(Integer)
    base_win_rate: Mapped[float | None] = mapped_column(Float)
    model_win_rate: Mapped[float | None] = mapped_column(Float)
    lift: Mapped[float | None] = mapped_column(Float)
    auc: Mapped[float | None] = mapped_column(Float)
    sharpe: Mapped[float | None] = mapped_column(Float)
    sortino: Mapped[float | None] = mapped_column(Float)
    max_drawdown: Mapped[float | None] = mapped_column(Float)
    profit_factor: Mapped[float | None] = mapped_column(Float)
    deflated_sharpe: Mapped[float | None] = mapped_column(Float)
    n_trials: Mapped[int | None] = mapped_column(Integer)
    is_champion: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false")
    model_path: Mapped[str | None] = mapped_column(String(256))
    note: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FuturesPaperPosition(Base):
    """Текущая бумажная позиция форка по (счёт, инструмент, интервал, стратегия) — Фаза D, 0033."""

    __tablename__ = "futures_paper_positions"
    __table_args__ = (
        UniqueConstraint("account", "asset_code", "interval", "source",
                         name="uq_futpaperpos_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account: Mapped[str] = mapped_column(String(32))
    asset_code: Mapped[str] = mapped_column(String(16))
    interval: Mapped[str] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(32))
    net_qty: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    avg_price: Mapped[float | None] = mapped_column(Float)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    last_price: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class FuturesPaperTrade(Base):
    """Журнал бумажных исполнений форка (аудит петли самообучения) — Фаза D, 0033."""

    __tablename__ = "futures_paper_trades"
    __table_args__ = (
        Index("ix_futpapertrades_key", "account", "asset_code", "interval", "source", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    account: Mapped[str] = mapped_column(String(32))
    asset_code: Mapped[str] = mapped_column(String(16))
    interval: Mapped[str] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(8))
    signed_qty: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    p_win: Mapped[float | None] = mapped_column(Float)
    realized_pnl: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(String(64))
    # Объективный вход (A5): уверенность совокупности независимых доказательств на входе + разбивка.
    conviction: Mapped[float | None] = mapped_column(Float)
    conviction_drivers: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class FuturesPaperEquity(Base):
    """Снимок эквити бумажного счёта во времени (Трек 2 / Пул 8, 0034) — ТРЕК-РЕКОРД песочницы.

    Один ряд на (счёт, час): mark-to-market эквити (реализ.+нереализ.), пик, просадка, валовая
    маржа. Без этого «доказанная результативность» не накапливается — кривую эквити для созревания
    строим отсюда (и будущая панель дашборда возьмёт данные здесь же). Идемпотентно (account, ts).
    """

    __tablename__ = "futures_paper_equity"
    __table_args__ = (
        UniqueConstraint("account", "ts", name="uq_futpaperequity_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account: Mapped[str] = mapped_column(String(32))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    equity: Mapped[float] = mapped_column(Float)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    open_positions: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    peak_equity: Mapped[float] = mapped_column(Float)
    drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    gross_margin: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class FuturesRiskState(Base):
    """Kill-switch бумажного счёта форка (Трек 2 / Пул 9 / B, 0035) — безоператорная защита.

    Один ряд на счёт: `halted` блокирует НОВЫЕ входы (выходы разрешены) при пробое жёстких лимитов
    или аномалии; сбрасывается вручную (`geo futures-intraday resume`). Причина/время — для аудита.
    """

    __tablename__ = "futures_risk_state"

    account: Mapped[str] = mapped_column(String(32), primary_key=True)
    halted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    reason: Mapped[str | None] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class MarketRegime(Base):
    """Режим рынка на день (G2/L5): материализованная разметка HMM по vol IMOEX/USD.

    Один ряд на день (идемпотентно перезаписывается при пересчёте — HMM размечает всю историю).
    Закрывает «режимы считаются на лету и выбрасываются»: копит историю режимов во времени
    для тренда/бэктеста. `state` 0=спокойный … K-1=кризис; `vol` — ср. vol IMOEX режима, %/день.
    """

    __tablename__ = "market_regimes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    day: Mapped[date] = mapped_column(Date, unique=True, index=True)
    state: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(32))
    vol: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RevenueSegment(Base):
    """Сегмент выручки эмитента (L2: состав компании — «из чего складывается выручка»).

    Одна строка на (компания, сегмент, период, источник) — повторный ввод идемпотентен.
    Надёжного скрейпера сегментов нет (smart-lab их не отдаёт) → ввод ручной/CLI
    (`geo segments add`) + сид флагманов. `value` — выручка сегмента в ₽, `share` — доля, %.
    """

    __tablename__ = "revenue_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    segment: Mapped[str] = mapped_column(String(64))        # «Корпоративный бизнес», «Розница»…
    value: Mapped[float] = mapped_column(Float)             # выручка сегмента, ₽
    share: Mapped[float | None] = mapped_column(Float)      # доля сегмента в выручке, %
    period: Mapped[str | None] = mapped_column(String(16))  # 2024 | 2024-H1 | …
    source: Mapped[str] = mapped_column(String(64), default="manual")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    company: Mapped[Company] = relationship(back_populates="segments")


class Person(Base):
    __tablename__ = "people"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    role: Mapped[str | None] = mapped_column(String(256))


class Event(Base):
    """Идентифицированное событие (санкции, дивиденды, отчётность...)."""

    __tablename__ = "events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Источник-статья (идемпотентность извлечения событий): одна статья → одно событие.
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), unique=True
    )
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(512))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    summary: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------------------- #
# Новости и их обогащение.
# --------------------------------------------------------------------------- #
class Story(Base):
    """Сюжет — кластер статей об одном событии (F6, Волна 2).

    Одно событие = N статей разных источников/рерайтов. Кластеризация онлайн:
    статья прикрепляется к сюжету ближайшей по эмбеддингу статьи (cosine ≤ порога)
    в скользящем окне, иначе открывает новый сюжет. Лечит double-counting
    neg_spike (Б10) и рерайты, прошедшие хеш-дедуп (Б11); `n_articles`/скорость
    прироста — сигнал значимости сюжета (velocity).
    """

    __tablename__ = "stories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Репрезентативный заголовок (первой статьи сюжета).
    title: Mapped[str] = mapped_column(String(1024))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    n_articles: Mapped[int] = mapped_column(Integer, default=1)


class Article(Base):
    """Очищенная новость, готовая к анализу."""

    __tablename__ = "articles"
    __table_args__ = (
        Index("ix_articles_published", "published_at"),
        # Дедуп near-duplicate: нормализованный хеш заголовка (одна новость от разных
        # лент/источников) — проверяется в окне `dedup_window_hours` при обработке.
        Index("ix_articles_content_hash", "content_hash"),
        Index("ix_articles_story", "story_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    raw_id: Mapped[int | None] = mapped_column(ForeignKey("raw_documents.id"))
    source: Mapped[str] = mapped_column(String(64), index=True)
    url: Mapped[str | None] = mapped_column(String(1024))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(1024))
    text: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Итоговая тональность статьи и категория события (заполняет nlp-слой).
    sentiment: Mapped[str | None] = mapped_column(String(16))
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    event_type: Mapped[str | None] = mapped_column(String(32), index=True)
    # Значимость новости в [0,1] (nlp.significance): фильтр инжеста, TTL-ретеншн, gate алертов.
    significance: Mapped[float | None] = mapped_column(Float, index=True)
    # Сюжет (F6): NULL — ещё не кластеризована (нет эмбеддинга/даты или джоб не дошёл).
    story_id: Mapped[int | None] = mapped_column(ForeignKey("stories.id", ondelete="SET NULL"))
    # F3 temporal anchoring: статус past/future/forecast/none (NULL — модель не настроена)
    # и дата СОБЫТИЯ из текста (NULL — не извлечена; потребители считают событием день
    # публикации — поведение до F3).
    temporal_status: Mapped[str | None] = mapped_column(String(8))
    event_date: Mapped[date | None] = mapped_column(Date)
    # F4 rumor/fact: фактологичность — fact/rumor/opinion (NULL — не размечена).
    # Влияет на надёжность источника (F7) и ранжирование сводки.
    factuality: Mapped[str | None] = mapped_column(String(16))
    # F7: идентичность источника тоньше source — канал для telegram (NULL у RSS, где
    # source уже различает). «Ключ источника» для надёжности = source_ref or source.
    source_ref: Mapped[str | None] = mapped_column(String(64), index=True)
    # F10: пост — прогноз брокера (целевая цена/рекомендация), а не новость. Такие статьи
    # не льются в общий сентимент-грунт сводки, а их числа извлекаются в таблицу forecasts.
    is_forecast: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    entities: Mapped[list[ArticleEntity]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )
    embedding: Mapped[Embedding | None] = relationship(
        back_populates="article", cascade="all, delete-orphan", uselist=False
    )


class ArticleEntity(Base):
    """Связь статья↔сущность с ролью и локальной тональностью.

    entity_type + entity_id указывают на конкретную сущность (asset/company/...).
    Это даёт ответ «какие новости относятся к активу X».
    """

    __tablename__ = "article_entities"
    __table_args__ = (
        Index("ix_artent_entity", "entity_type", "entity_id"),
        UniqueConstraint("article_id", "entity_type", "entity_id", name="uq_artent"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    entity_type: Mapped[str] = mapped_column(String(16))   # см. core.types.EntityType
    entity_id: Mapped[int] = mapped_column(Integer)
    mention: Mapped[str | None] = mapped_column(String(256))  # как упомянуто в тексте
    # Тональность относительно ЭТОЙ сущности (F1 aspect-sentiment, Волна 2);
    # до F1 — копия тональности статьи.
    sentiment: Mapped[str | None] = mapped_column(String(16))
    relevance: Mapped[float | None] = mapped_column(Float)
    # Салиентность (F2): TRUE — главный объект, FALSE — фоновое упоминание,
    # NULL — не классифицировано (трактуется как салиентно).
    salient: Mapped[bool | None] = mapped_column(Boolean)

    article: Mapped[Article] = relationship(back_populates="entities")


class ArticleNumber(Base):
    """Числовой факт из текста статьи (F5, Волна 3; nlp/numeric.py).

    kind — dividend (RUB на акцию) / key_rate (pct) / deal_amount (валюта сделки);
    value — нормализованное число (суммы в абсолюте: «35 млрд руб» → 3.5e10).
    Извлечение rule-based и детерминированное; uq_artnum делает переразметку
    идемпотентной.
    """

    __tablename__ = "article_numbers"
    __table_args__ = (
        UniqueConstraint("article_id", "kind", "value", "unit", name="uq_artnum"),
        Index("ix_artnum_kind", "kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(8))   # RUB/USD/EUR/CNY/pct
    snippet: Mapped[str | None] = mapped_column(String(200))


class Forecast(Base):
    """Прогноз брокера для (статья, актив) — F10 (Волна 4; nlp/forecast.py + numeric.py).

    Извлекается из брокерских каналов (Сбер/Т-/БКС Инвестиции), которые роутер
    `is_forecast_post` отнёс к прогнозам: целевая цена / ожидаемый дивиденд / ставка.
    `value`/`unit` — как в тексте (целевая цена в руб/$, дивиденд в руб). `target_date` —
    горизонт прогноза из F3 (event_date), NULL — не указан. Хранилище ОЖИДАНИЙ; позже
    сравнивается с фактом (surprise = факт − прогноз) — методология «данные→модель».
    uq_forecast делает переразметку идемпотентной.
    """

    __tablename__ = "forecasts"
    __table_args__ = (
        UniqueConstraint("article_id", "asset_id", "kind", "value", name="uq_forecast"),
        Index("ix_forecast_asset", "asset_id"),
        Index("ix_forecast_kind", "kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))   # target_price/dividend/key_rate
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(8))    # RUB/USD/EUR/CNY/pct
    target_date: Mapped[date | None] = mapped_column(Date)
    source_channel: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Embedding(Base):
    """Векторное представление статьи для семантического поиска (RAG)."""

    __tablename__ = "embeddings"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), unique=True
    )
    model: Mapped[str] = mapped_column(String(128))
    vector: Mapped[list[float]] = mapped_column(HALFVEC(EMBEDDING_DIM))

    article: Mapped[Article] = relationship(back_populates="embedding")


# --------------------------------------------------------------------------- #
# Тайм-серии (становятся hypertable'ами в миграции).
# --------------------------------------------------------------------------- #
class Price(Base):
    """OHLCV-свеча инструмента."""

    __tablename__ = "prices"
    __table_args__ = (
        UniqueConstraint("asset_id", "ts", "interval", name="uq_price_point"),
        Index("ix_prices_asset_ts", "asset_id", "ts"),
    )

    # ts входит в PK: TimescaleDB требует колонку партиционирования во всех уникальных индексах.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    interval: Mapped[str] = mapped_column(String(8), default="1d")   # 1d, 1h, 10m...
    open: Mapped[float] = mapped_column(Numeric(18, 6))
    high: Mapped[float] = mapped_column(Numeric(18, 6))
    low: Mapped[float] = mapped_column(Numeric(18, 6))
    close: Mapped[float] = mapped_column(Numeric(18, 6))
    volume: Mapped[float | None] = mapped_column(Numeric(20, 2))


class MacroSeries(Base):
    """Значение макропоказателя на дату (ключевая ставка, инфляция и т.п.)."""

    __tablename__ = "macro_series"
    __table_args__ = (UniqueConstraint("indicator", "ts", name="uq_macro_point"),)

    # ts входит в PK: TimescaleDB требует колонку партиционирования во всех уникальных индексах.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    indicator: Mapped[str] = mapped_column(String(64), index=True)   # key_rate, cpi, brent...
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    value: Mapped[float] = mapped_column(Numeric(18, 6))
    unit: Mapped[str | None] = mapped_column(String(16))


class FxRate(Base):
    """Курс валюты к рублю на дату (ЦБ РФ)."""

    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("currency", "ts", name="uq_fx_point"),)

    # ts входит в PK: TimescaleDB требует колонку партиционирования во всех уникальных индексах.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    currency: Mapped[str] = mapped_column(String(3), index=True)     # USD, EUR, CNY
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    value: Mapped[float] = mapped_column(Numeric(18, 6))


# --------------------------------------------------------------------------- #
# Производная аналитика.
# --------------------------------------------------------------------------- #
class AssetContext(Base):
    """Скользящий нарратив-контекст по активу (инкрементально обновляется LLM).

    Хранится версионно: новая запись на каждое обновление, актуальная — с max(version).
    """

    __tablename__ = "asset_context"
    __table_args__ = (Index("ix_ctx_asset_ver", "asset_id", "version"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    narrative: Mapped[str] = mapped_column(Text)                     # связный текст-контекст
    drivers: Mapped[dict | None] = mapped_column(JSON)  # факторы
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventImpact(Base):
    """Оценка влияния события на актив (для анализа факторов)."""

    __tablename__ = "event_impacts"
    __table_args__ = (UniqueConstraint("event_id", "asset_id", name="uq_event_impact"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    direction: Mapped[str | None] = mapped_column(String(16))        # positive/negative/mixed
    magnitude: Mapped[float | None] = mapped_column(Float)           # сила влияния 0..1
    rationale: Mapped[str | None] = mapped_column(Text)


class NewsOutcome(Base):
    """Рыночный исход новости для пары (статья, актив) — авто-золото E2 (Волна 1 v2.0).

    Размечается ежедневным джобом по ФАКТИЧЕСКИМ ценам: форвардные доходности от
    последнего закрытия ПЕРЕД новостью (pre-news close) на горизонты 1/3/5 торговых
    дней — сырые (`ret_*`) и market-adjusted (`abn_* = ret − β·ret_IMOEX`). Это
    объективные метки для significance v3 (E3), event study (E1) и надёжности
    источников (F7): рынок — учитель вместо LLM.
    """

    __tablename__ = "news_outcomes"
    __table_args__ = (
        UniqueConstraint("article_id", "asset_id", name="uq_news_outcome"),
        Index("ix_news_outcomes_asset", "asset_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    # Торговая дата новости (после закрытия сессии — следующий день, без lookahead)
    # и дата базового (pre-news) закрытия, от которого считаются доходности.
    event_date: Mapped[datetime] = mapped_column(Date)
    base_date: Mapped[datetime] = mapped_column(Date)
    # Форвардные доходности в процентах: close(base+k)/close(base) − 1.
    ret_1d: Mapped[float | None] = mapped_column(Float)
    ret_3d: Mapped[float | None] = mapped_column(Float)
    ret_5d: Mapped[float | None] = mapped_column(Float)
    # Market-adjusted (минус β×IMOEX за тот же отрезок); None — индекс был недоступен.
    abn_1d: Mapped[float | None] = mapped_column(Float)
    abn_3d: Mapped[float | None] = mapped_column(Float)
    abn_5d: Mapped[float | None] = mapped_column(Float)
    # Бета к IMOEX (окно 250 торговых дней до base_date; None — данных не хватило, β=1).
    beta: Mapped[float | None] = mapped_column(Float)
    # Копия релевантности связи статья↔актив на момент разметки (вес примера в датасете).
    relevance: Mapped[float | None] = mapped_column(Float)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AlertOutcome(Base):
    """Фактический исход алерта (E4, Волна 1): двинулся ли актив после срабатывания.

    Скорится джобом через `horizon_days` торговых дней: движение цены от закрытия
    дня алерта, сырое и за вычетом IMOEX. `hit` — |движение| ≥ порога
    (GEO_ALERT_OUTCOME_MOVE_PCT) — основа постоянной метрики precision по типам
    алертов (первая объективная обратная связь алерт-системы).
    """

    __tablename__ = "alert_outcomes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    alert_id: Mapped[int] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"), unique=True
    )
    ticker: Mapped[str] = mapped_column(String(32), index=True)
    base_date: Mapped[datetime] = mapped_column(Date)
    horizon_days: Mapped[int] = mapped_column(Integer)
    move_pct: Mapped[float] = mapped_column(Float)
    abn_move_pct: Mapped[float | None] = mapped_column(Float)
    hit: Mapped[bool] = mapped_column(Boolean)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CalendarEvent(Base):
    """Запланированное событие календаря (H2, Волна 3): заседание ЦБ, отсечка, отчётность.

    В отличие от `events` (извлечённых из новостей ПОСЛЕ публикации), календарь
    знает дату ЗАРАНЕЕ — аналитика становится проактивной («завтра заседание ЦБ»),
    а event study (E1) получает точные опорные даты. `dedup_key` —
    `{kind}:{ticker|CBR}:{event_date}`, upsert идемпотентен; прошлые события не
    удаляются (история — якоря для E1/F10).
    """

    __tablename__ = "calendar_events"
    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_calendar_dedup"),
        Index("ix_calendar_date", "event_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # cbr_rate_meeting / dividend_cutoff / earnings (последний — из H1).
    kind: Mapped[str] = mapped_column(String(32), index=True)
    event_date: Mapped[datetime] = mapped_column(Date)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"))  # None — макро-событие
    title: Mapped[str] = mapped_column(String(512))
    source: Mapped[str] = mapped_column(String(32))                        # cbr / moex / ...
    dedup_key: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict | None] = mapped_column(JSON)                     # value, currency и т.п.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# --------------------------------------------------------------------------- #
# Алерты (M5.3): сработавшие триггеры и статус доставки.
# --------------------------------------------------------------------------- #
class AlertRecord(Base):
    """Зафиксированный алерт (движение цены / всплеск негатива / новое событие).

    `dedup_key` — детерминированный ключ срабатывания (с временным окном внутри),
    уникальный: повторный прогон движка не создаёт дубль и не шлёт повторное
    уведомление. `channels` — куда фактически доставлено.
    """

    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_alert_dedup"),
        Index("ix_alerts_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    alert_type: Mapped[str] = mapped_column(String(32), index=True)  # price_move/neg_spike/event
    ticker: Mapped[str | None] = mapped_column(String(32), index=True)  # None для рыночных алертов
    # Владелец персонального алерта (5c): NULL — broadcast всем; иначе доставляется только ему.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    severity: Mapped[str] = mapped_column(String(16), default="info")   # info/warning/critical
    title: Mapped[str] = mapped_column(String(512))
    message: Mapped[str] = mapped_column(Text)
    dedup_key: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict | None] = mapped_column(JSON)                  # сырые числа триггера
    channels: Mapped[list | None] = mapped_column(JSON)                # доставлено: ["telegram"]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Подтверждение (ack) оператором: NULL = не просмотрен.
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class User(Base):
    """Пользователь бота (Волна 5b): идентичность Telegram → доступ к системе.

    `/start` регистрирует/обновляет запись по `telegram_user_id` (в личке чата он же = chat_id).
    `allowed` — авторизация (отвечаем/доставляем алерты только разрешённым). `role` admin/user
    (admin — chat_id из стартового allowlist настроек). До 5b система была одно-пользовательской.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("telegram_user_id", name="uq_user_tg"),
        Index("ix_users_chat", "chat_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger)
    chat_id: Mapped[str] = mapped_column(String(32))
    username: Mapped[str | None] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16), default="user")        # user / admin
    allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AlertMute(Base):
    """Правило подавления алертов: по тикеру, типу или паре (тикер+тип).

    Замьюченные алерты всё равно фиксируются в `alerts` (история не теряется),
    но не рассылаются по каналам. `until=NULL` — бессрочно, иначе действует до момента.
    `user_id` (5b): NULL — глобальный mute (подавляет для всех, прежнее поведение); иначе —
    личный mute конкретного пользователя (не доставляется только ему).
    """

    __tablename__ = "alert_mutes"
    __table_args__ = (
        Index("ix_alert_mutes_scope", "scope_type", "scope_value"),
        Index("ix_alert_mutes_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(16))   # ticker / type / ticker_type
    scope_value: Mapped[str] = mapped_column(String(96))  # SBER / price_move / SBER:price_move
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")        # NULL = глобальный
    )
    reason: Mapped[str | None] = mapped_column(String(256))
    until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # NULL = бессрочно
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --------------------------------------------------------------------------- #
# Граф связей (lite): отвечает «какие факторы влияют на актив».
# --------------------------------------------------------------------------- #
class Relation(Base):
    """Направленная связь между двумя сущностями графа.

    Пример: (asset:SBER) --belongs_to--> (sector:Банки);
            (country:RUS) --sanctioned_by--> (country:USA).
    """

    __tablename__ = "relations"
    __table_args__ = (
        Index("ix_rel_subject", "subject_type", "subject_id"),
        Index("ix_rel_object", "object_type", "object_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(16))
    subject_id: Mapped[int] = mapped_column(Integer)
    predicate: Mapped[str] = mapped_column(String(64))              # belongs_to, affected_by...
    object_type: Mapped[str] = mapped_column(String(16))
    object_id: Mapped[int] = mapped_column(Integer)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
