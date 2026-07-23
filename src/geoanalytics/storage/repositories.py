"""Репозитории — тонкий слой доступа к данным поверх моделей.

Инкапсулируют типовые запросы, чтобы остальные слои не писали SQL напрямую.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from geoanalytics.nlp.text import normalized_text
from geoanalytics.storage.models import (
    Article,
    ArticleEntity,
    Asset,
    AssetFundamental,
    CashBalance,
    Company,
    EvalRun,
    FactorScore,
    Forecast,
    FuturesCandle,
    FuturesDecision,
    FuturesModelRun,
    FuturesOrderbook,
    FuturesPaperEquity,
    FuturesPaperPosition,
    FuturesPaperTrade,
    FuturesRiskState,
    MarketRegime,
    PortfolioPosition,
    PortfolioSnapshot,
    Price,
    RawDocument,
    RevenueSegment,
    User,
)


def content_hash(text: str) -> str:
    """Стабильный хеш текста для дедупликации сырых документов."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalized_hash(text: str) -> str:
    """Хеш НОРМАЛИЗОВАННОГО текста — для дедупа near-duplicate (одна новость от разных
    лент/перепостов с косметическими отличиями). Идемпотентен по `normalized_text`."""
    return hashlib.sha256(normalized_text(text).encode("utf-8")).hexdigest()


class RawRepository:
    """Доступ к сырым документам (raw-слой)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_if_new(
        self,
        *,
        source: str,
        raw_text: str,
        external_id: str | None = None,
        payload: dict | None = None,
    ) -> RawDocument | None:
        """Вставляет документ, если его ещё нет.

        Дедуп двухуровневый: (1) по `(source, external_id)` — стабильный id издателя,
        ловит перепубликации с косметически разным текстом (`&nbsp;`, правки), которые
        точный content_hash пропускал ~98% дублей; (2) по `(source, content_hash)` —
        фолбэк для источников без external_id. Возвращает документ или None (дубликат).
        """
        if external_id is not None:
            dup = self.session.scalar(
                select(RawDocument.id).where(
                    RawDocument.source == source,
                    RawDocument.external_id == external_id,
                ).limit(1)
            )
            if dup is not None:
                return None
        digest = content_hash(raw_text)
        stmt = (
            pg_insert(RawDocument)
            .values(
                source=source,
                external_id=external_id,
                content_hash=digest,
                raw_text=raw_text,
                payload=payload,
            )
            .on_conflict_do_nothing(constraint="uq_raw_doc_hash")
            .returning(RawDocument.id)
        )
        result = self.session.execute(stmt).scalar_one_or_none()
        if result is None:
            return None
        return self.session.get(RawDocument, result)

    def unprocessed(self, limit: int = 100) -> list[RawDocument]:
        """Сырые документы, ещё не прошедшие обработку."""
        stmt = (
            select(RawDocument)
            .where(RawDocument.processed.is_(False))
            .order_by(RawDocument.fetched_at)
            .limit(limit)
        )
        return list(self.session.scalars(stmt))


class ArticleRepository:
    """Доступ к новостям и их связям с сущностями."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def recent(self, hours: int = 24, limit: int = 100) -> list[Article]:
        """Последние новости за окно `hours`."""
        since = datetime.now(UTC) - timedelta(hours=hours)
        stmt = (
            select(Article)
            .where(Article.published_at >= since)
            .order_by(Article.published_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    def for_asset(self, asset_id: int, hours: int = 168, limit: int = 50) -> list[Article]:
        """Новости, связанные с активом, за окно `hours`."""
        since = datetime.now(UTC) - timedelta(hours=hours)
        stmt = (
            select(Article)
            .join(ArticleEntity, ArticleEntity.article_id == Article.id)
            .where(
                ArticleEntity.entity_type == "asset",
                ArticleEntity.entity_id == asset_id,
                Article.published_at >= since,
            )
            .order_by(Article.published_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))


class AssetRepository:
    """Доступ к торгуемым инструментам."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def by_ticker(self, ticker: str) -> Asset | None:
        stmt = select(Asset).where(Asset.ticker == ticker.upper())
        return self.session.scalars(stmt).first()

    def latest_price(self, asset_id: int, interval: str = "1d") -> Price | None:
        # Фильтр по interval (дефолт дневной): не смешивать таймфреймы, если в prices появятся
        # интрадей-бары (защитно — интрадей фьючерсов живёт в отдельной futures_candles).
        stmt = (
            select(Price)
            .where(Price.asset_id == asset_id, Price.interval == interval)
            .order_by(Price.ts.desc())
            .limit(1)
        )
        return self.session.scalars(stmt).first()


class PortfolioRepository:
    """Позиции виртуального портфеля (J1; per-user — 5c).

    Все операции скоупятся `user_id`: None — портфель владельца (дашборд/CLI), иначе личный
    портфель пользователя. Одна строка на актив в рамках портфеля.
    """

    def __init__(self, session: Session, *, user_id: int | None = None) -> None:
        self.session = session
        self.user_id = user_id

    def _scope(self, stmt):
        """Добавить фильтр по user_id (IS NULL для портфеля владельца)."""
        col = PortfolioPosition.user_id
        return stmt.where(col.is_(None) if self.user_id is None else col == self.user_id)

    def upsert_position(
        self, ticker: str, quantity: float, avg_price: float | None = None
    ) -> PortfolioPosition | None:
        """Добавляет/наращивает позицию в портфеле скоупа. None — тикера нет в assets.

        Повторный add суммирует количество; avg_price пересчитывается
        средневзвешенно, если задана и у старой, и у новой части. Количество должно быть
        положительным (контракт «наращивание»; частичная продажа не поддержана, удаление —
        целиком через remove_position) — иначе ValueError, чтобы не создать молча нулевую/
        короткую позицию (Б-аудит)."""
        if quantity <= 0:
            raise ValueError("количество должно быть положительным")
        asset = self.session.scalars(
            select(Asset).where(Asset.ticker == ticker.upper())
        ).first()
        if asset is None:
            return None
        pos = self.session.scalars(
            self._scope(select(PortfolioPosition)
                        .where(PortfolioPosition.asset_id == asset.id))
        ).first()
        if pos is None:
            pos = PortfolioPosition(asset_id=asset.id, quantity=quantity,
                                    avg_price=avg_price, user_id=self.user_id)
            self.session.add(pos)
        else:
            old_qty, old_avg = pos.quantity, pos.avg_price
            pos.quantity = old_qty + quantity
            if avg_price is not None and old_avg is not None and pos.quantity > 0:
                pos.avg_price = (float(old_avg) * old_qty + avg_price * quantity) / pos.quantity
            elif avg_price is not None:
                pos.avg_price = avg_price
        self.session.flush()
        return pos

    def remove_position(self, ticker: str) -> bool:
        """Удаляет позицию целиком из портфеля скоупа. False — её и не было."""
        pos = self.session.scalars(
            self._scope(select(PortfolioPosition).join(Asset)
                        .where(Asset.ticker == ticker.upper()))
        ).first()
        if pos is None:
            return False
        self.session.delete(pos)
        return True

    def list_positions(self) -> list[tuple[Asset, PortfolioPosition]]:
        """Все позиции портфеля скоупа с активами, по тикеру."""
        rows = self.session.execute(
            self._scope(
                select(Asset, PortfolioPosition)
                .join(PortfolioPosition, PortfolioPosition.asset_id == Asset.id)
            ).order_by(Asset.ticker)
        )
        return [(a, p) for a, p in rows]

    def owners_with_positions(self) -> list[int]:
        """user_id всех ЛИЧНЫХ портфелей с позициями (для per-owner алертов/дайджеста 5c)."""
        rows = self.session.scalars(
            select(PortfolioPosition.user_id)
            .where(PortfolioPosition.user_id.isnot(None))
            .distinct()
        )
        return list(rows)


class CashBalanceRepository:
    """Денежные/валютные балансы портфеля (расширение состава). Скоупится `user_id` как
    [[PortfolioRepository]]: None — баланс владельца, иначе личный бот-юзера."""

    def __init__(self, session: Session, *, user_id: int | None = None) -> None:
        self.session = session
        self.user_id = user_id

    def _scope(self, stmt):
        col = CashBalance.user_id
        return stmt.where(col.is_(None) if self.user_id is None else col == self.user_id)

    def set_balance(self, currency: str, amount: float) -> CashBalance | None:
        """Установить баланс валюты (перезаписывает, не суммирует). amount ≤ 0 — удаляет
        строку (нулевой/отрицательный остаток не храним). None — если удалили/нечего."""
        ccy = currency.upper()
        row = self.session.scalars(
            self._scope(select(CashBalance).where(CashBalance.currency == ccy))
        ).first()
        if amount <= 0:
            if row is not None:
                self.session.delete(row)
            return None
        if row is None:
            row = CashBalance(currency=ccy, amount=amount, user_id=self.user_id)
            self.session.add(row)
        else:
            row.amount = amount
        self.session.flush()
        return row

    def remove(self, currency: str) -> bool:
        """Удалить баланс валюты целиком. False — её и не было."""
        row = self.session.scalars(
            self._scope(select(CashBalance).where(CashBalance.currency == currency.upper()))
        ).first()
        if row is None:
            return False
        self.session.delete(row)
        return True

    def list_balances(self) -> list[tuple[str, float]]:
        """[(валюта, сумма)] портфеля скоупа, по валюте."""
        rows = self.session.execute(
            self._scope(select(CashBalance.currency, CashBalance.amount))
            .order_by(CashBalance.currency)
        )
        return [(c, float(a)) for c, a in rows]


class PortfolioSnapshotRepository:
    """Дневные снимки стоимости портфеля (хвост просмотра, миграция 0023). Скоупится `user_id`
    как [[PortfolioRepository]]: None — портфель владельца, иначе личный бот-юзера."""

    def __init__(self, session: Session, *, user_id: int | None = None) -> None:
        self.session = session
        self.user_id = user_id

    def _scope(self, stmt):
        col = PortfolioSnapshot.user_id
        return stmt.where(col.is_(None) if self.user_id is None else col == self.user_id)

    def upsert(self, snapshot_date: date, total_value_rub: float,
               cost_basis_rub: float | None = None) -> PortfolioSnapshot:
        """Записать/перезаписать снимок за дату (идемпотентно — повторный job за день не плодит
        строк). Возвращает строку снимка."""
        row = self.session.scalars(
            self._scope(select(PortfolioSnapshot)
                        .where(PortfolioSnapshot.snapshot_date == snapshot_date))
        ).first()
        if row is None:
            row = PortfolioSnapshot(
                user_id=self.user_id, snapshot_date=snapshot_date,
                total_value_rub=total_value_rub, cost_basis_rub=cost_basis_rub)
            self.session.add(row)
        else:
            row.total_value_rub = total_value_rub
            row.cost_basis_rub = cost_basis_rub
        self.session.flush()
        return row

    def history(self, *, limit: int = 365) -> list[tuple[date, float, float | None]]:
        """[(дата, стоимость, база)] портфеля скоупа по возрастанию даты (последние `limit`)."""
        rows = self.session.execute(
            self._scope(select(PortfolioSnapshot.snapshot_date,
                               PortfolioSnapshot.total_value_rub,
                               PortfolioSnapshot.cost_basis_rub))
            .order_by(PortfolioSnapshot.snapshot_date.desc()).limit(limit)
        ).all()
        out = [(d, float(v), float(c) if c is not None else None) for d, v, c in rows]
        out.reverse()
        return out


class EvalRunRepository:
    """Журнал прогонов непрерывной оценки (ось I/I2, миграция 0024). Append-only."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def record(self, model_name: str, metric_name: str, value: float, n_samples: int,
               window_days: int | None = None) -> EvalRun:
        """Записать прогон метрики (новая строка)."""
        row = EvalRun(model_name=model_name, metric_name=metric_name, value=value,
                      n_samples=n_samples, window_days=window_days)
        self.session.add(row)
        self.session.flush()
        return row

    def recent(self, model_name: str, metric_name: str, *,
               limit: int = 12) -> list[tuple[datetime, float, int]]:
        """[(дата, значение, n)] последних прогонов метрики, СВЕЖИЕ первыми."""
        rows = self.session.execute(
            select(EvalRun.created_at, EvalRun.value, EvalRun.n_samples)
            .where(EvalRun.model_name == model_name, EvalRun.metric_name == metric_name)
            .order_by(EvalRun.created_at.desc()).limit(limit)
        ).all()
        return [(d, float(v), int(n)) for d, v, n in rows]


class AssetFundamentalRepository:
    """Фундаментальные метрики эмитентов из отчётов (H5, миграция 0025)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, asset_id: int, metric: str, value: float, unit: str, *,
               period: str | None = None, source: str = "pdf",
               snippet: str | None = None) -> AssetFundamental:
        """Записать/перезаписать метрику за (актив, метрика, период, источник) — идемпотентно."""
        row = self.session.scalars(
            select(AssetFundamental).where(
                AssetFundamental.asset_id == asset_id,
                AssetFundamental.metric == metric,
                AssetFundamental.period.is_(period) if period is None
                else AssetFundamental.period == period,
                AssetFundamental.source == source,
            )
        ).first()
        if row is None:
            row = AssetFundamental(asset_id=asset_id, metric=metric, value=value, unit=unit,
                                   period=period, source=source, snippet=snippet)
            self.session.add(row)
        else:
            row.value, row.unit, row.snippet = value, unit, snippet
        self.session.flush()
        return row

    def latest_for_asset(self, asset_id: int) -> list[AssetFundamental]:
        """Свежайшая запись по каждой метрике актива (по периоду ↓, затем дате ↓)."""
        rows = self.session.scalars(
            select(AssetFundamental).where(AssetFundamental.asset_id == asset_id)
            .order_by(AssetFundamental.period.desc().nulls_last(),
                      AssetFundamental.created_at.desc())
        ).all()
        out: dict[str, AssetFundamental] = {}
        for r in rows:
            out.setdefault(r.metric, r)       # первая (свежайшая) на метрику
        return list(out.values())


class FactorScoreRepository:
    """Кросс-секционные факторные скоры активов во времени (L3)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def delete_day(self, day) -> None:
        """Удалить срез за день (для идемпотентного пересчёта)."""
        self.session.execute(delete(FactorScore).where(FactorScore.day == day))

    def recorded_days(self, since) -> set:
        """Множество дней с записанным срезом начиная с `since` (для самозалечивания пропусков)."""
        rows = self.session.execute(
            select(FactorScore.day).where(FactorScore.day >= since).distinct()
        )
        return {d for (d,) in rows}

    def add(self, day, asset_id: int, factor: str, zscore: float,
            percentile: float | None) -> FactorScore:
        row = FactorScore(day=day, asset_id=asset_id, factor=factor,
                          zscore=zscore, percentile=percentile)
        self.session.add(row)
        return row

    def latest_for_asset(self, asset_id: int) -> dict[str, FactorScore]:
        """Факторные скоры актива за свежайший доступный день → {factor: row}."""
        latest_day = self.session.scalar(
            select(func.max(FactorScore.day)).where(FactorScore.asset_id == asset_id)
        )
        if latest_day is None:
            return {}
        rows = self.session.scalars(
            select(FactorScore).where(FactorScore.asset_id == asset_id,
                                      FactorScore.day == latest_day)
        ).all()
        return {r.factor: r for r in rows}

    def series_for_asset(self, asset_id: int, factor: str, *, days: int = 180) -> list[FactorScore]:
        """Ряд скоров фактора актива по дням (для тренда; L5)."""
        rows = self.session.scalars(
            select(FactorScore).where(FactorScore.asset_id == asset_id,
                                      FactorScore.factor == factor)
            .order_by(FactorScore.day.desc()).limit(days)
        ).all()
        return list(reversed(rows))


class FuturesCandleRepository:
    """Интрадей-свечи фьючерсов по контрактам (Трек 2 / T2.1)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_many(self, rows: list[dict], *, chunk: int = 1000) -> int:
        """Батч-вставка свечей (идемпотентно по contract_secid+ts+interval). Число НОВЫХ строк."""
        total = 0
        for i in range(0, len(rows), chunk):
            batch = rows[i:i + chunk]
            if not batch:
                continue
            # RETURNING: при ON CONFLICT DO NOTHING rowcount у мульти-вставки недостоверен (−1),
            # поэтому считаем реально вставленные строки по возвращённым id.
            stmt = (pg_insert(FuturesCandle).values(batch)
                    .on_conflict_do_nothing(index_elements=["contract_secid", "ts", "interval"])
                    .returning(FuturesCandle.id))
            total += len(self.session.execute(stmt).fetchall())
        return total

    def contracts(self, asset_code: str, interval: str) -> list[tuple[str, object]]:
        """Контракты с данными для asset_code/interval: [(secid, expiry)] по экспирации."""
        rows = self.session.execute(
            select(FuturesCandle.contract_secid, FuturesCandle.expiry)
            .where(FuturesCandle.asset_code == asset_code, FuturesCandle.interval == interval)
            .distinct()
        ).all()
        return sorted(((s, e) for s, e in rows), key=lambda r: (r[1] is None, r[1]))

    def contract_series(self, contract_secid: str, interval: str) -> list[FuturesCandle]:
        """Свечи одного контракта по возрастанию времени."""
        return list(self.session.scalars(
            select(FuturesCandle).where(
                FuturesCandle.contract_secid == contract_secid,
                FuturesCandle.interval == interval,
            ).order_by(FuturesCandle.ts)
        ))


class FuturesOrderbookRepository:
    """Снимки стакана (L2 depth) фьючерсов (Трек 2, миграция 0037)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, *, asset_code: str, contract_secid: str, ts, best_bid=None, best_ask=None,
            spread=None, bid_vol=None, ask_vol=None, imbalance=None, levels=None,
            bids=None, asks=None) -> None:
        """Записать снимок стакана (идемпотентность — на уникальном индексе contract_secid+ts)."""
        self.session.add(FuturesOrderbook(
            asset_code=asset_code, contract_secid=contract_secid, ts=ts,
            best_bid=best_bid, best_ask=best_ask, spread=spread, bid_vol=bid_vol,
            ask_vol=ask_vol, imbalance=imbalance, levels=levels, bids=bids, asks=asks))

    def latest(self, asset_code: str) -> FuturesOrderbook | None:
        """Свежайший снимок стакана инструмента (для Фазы C / панели)."""
        return self.session.scalars(select(FuturesOrderbook).where(
            FuturesOrderbook.asset_code == asset_code).order_by(
            FuturesOrderbook.ts.desc()).limit(1)).first()

    def count(self) -> int:
        return self.session.scalar(select(func.count()).select_from(FuturesOrderbook)) or 0


class FuturesDecisionRepository:
    """Лог торговых решений фьючерсного форка + признаки + исходы (Трек 2 / T2.3)."""

    _UPDATE_COLS = ("contract_secid", "action", "signed_qty", "price", "features",
                    "horizon_bars", "outcome_ts", "outcome_return_pct", "outcome_pnl_rub", "label")

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_many(self, rows: list[dict], *, chunk: int = 1000) -> int:
        """Батч-запись решений. Идемпотентно по (source, asset_code, interval, ts); на конфликте
        ОБНОВЛЯЕТ признаки/исход (дозревшая разметка перетирает старую). Число строк."""
        total = 0
        for i in range(0, len(rows), chunk):
            batch = rows[i:i + chunk]
            if not batch:
                continue
            stmt = pg_insert(FuturesDecision).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["source", "asset_code", "interval", "ts"],
                set_={c: getattr(stmt.excluded, c) for c in self._UPDATE_COLS},
            ).returning(FuturesDecision.id)
            total += len(self.session.execute(stmt).fetchall())
        return total

    def recent(self, asset_code: str, interval: str, *, source: str | None = None,
               limit: int = 20) -> list[FuturesDecision]:
        """Свежие решения по asset_code/interval (опц. конкретной политики), новые сверху."""
        stmt = select(FuturesDecision).where(
            FuturesDecision.asset_code == asset_code, FuturesDecision.interval == interval)
        if source:
            stmt = stmt.where(FuturesDecision.source == source)
        stmt = stmt.order_by(FuturesDecision.ts.desc()).limit(limit)
        return list(self.session.scalars(stmt))

    def labeled(self, *, asset_code: str | None = None,
                source: str | None = None) -> list[FuturesDecision]:
        """Размеченные решения (label IS NOT NULL) — обучающая выборка для T2.4."""
        stmt = select(FuturesDecision).where(FuturesDecision.label.is_not(None))
        if asset_code:
            stmt = stmt.where(FuturesDecision.asset_code == asset_code)
        if source:
            stmt = stmt.where(FuturesDecision.source == source)
        return list(self.session.scalars(stmt.order_by(FuturesDecision.ts)))


class FuturesModelRunRepository:
    """Реестр OOS-оценок политик форка во времени (Трек 2 / Фаза B)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, run: dict) -> int:
        """Записать прогон оценки. Возвращает id новой строки."""
        rec = FuturesModelRun(**run)
        self.session.add(rec)
        self.session.flush()
        return rec.id

    def recent(self, *, source: str | None = None, asset_code: str | None = None,
               interval: str | None = None, limit: int = 20) -> list[FuturesModelRun]:
        """Свежие прогоны (новые сверху), опц. по ключу политики/актива/интервала."""
        stmt = select(FuturesModelRun)
        if source:
            stmt = stmt.where(FuturesModelRun.source == source)
        if asset_code:
            stmt = stmt.where(FuturesModelRun.asset_code == asset_code)
        if interval:
            stmt = stmt.where(FuturesModelRun.interval == interval)
        stmt = stmt.order_by(FuturesModelRun.ts.desc()).limit(limit)
        return list(self.session.scalars(stmt))

    def mark_champion(self, run_id: int, *, source: str, asset_code: str | None,
                      interval: str) -> None:
        """Пометить прогон чемпионом, сняв флаг с прежних для того же ключа."""
        prior = select(FuturesModelRun).where(
            FuturesModelRun.source == source, FuturesModelRun.interval == interval,
            FuturesModelRun.is_champion.is_(True))
        prior = (prior.where(FuturesModelRun.asset_code == asset_code) if asset_code
                 else prior.where(FuturesModelRun.asset_code.is_(None)))
        for rec in self.session.scalars(prior):
            rec.is_champion = False
        rec = self.session.get(FuturesModelRun, run_id)
        if rec is not None:
            rec.is_champion = True

    def champion(self, *, source: str, asset_code: str | None,
                 interval: str) -> FuturesModelRun | None:
        """Текущий чемпион для ключа политики (или None)."""
        stmt = select(FuturesModelRun).where(
            FuturesModelRun.source == source, FuturesModelRun.interval == interval,
            FuturesModelRun.is_champion.is_(True))
        stmt = (stmt.where(FuturesModelRun.asset_code == asset_code) if asset_code
                else stmt.where(FuturesModelRun.asset_code.is_(None)))
        return self.session.scalars(stmt.limit(1)).first()


class FuturesPaperRepository:
    """Бумажный счёт форка: позиции + журнал сделок (Трек 2 / Фаза D)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def position(self, account: str, asset_code: str, interval: str,
                 source: str) -> FuturesPaperPosition | None:
        return self.session.scalars(select(FuturesPaperPosition).where(
            FuturesPaperPosition.account == account,
            FuturesPaperPosition.asset_code == asset_code,
            FuturesPaperPosition.interval == interval,
            FuturesPaperPosition.source == source).limit(1)).first()

    def last_entry_ts(self, account: str, asset_code: str, interval: str,
                      source: str) -> datetime | None:
        """ts последнего реального входа (reason='entry') позиции — для овернайт-проверки сессии."""
        return self.session.scalar(select(FuturesPaperTrade.ts).where(
            FuturesPaperTrade.account == account,
            FuturesPaperTrade.asset_code == asset_code,
            FuturesPaperTrade.interval == interval,
            FuturesPaperTrade.source == source,
            FuturesPaperTrade.reason == "entry",
            FuturesPaperTrade.signed_qty != 0).order_by(FuturesPaperTrade.ts.desc()).limit(1))

    def upsert_position(self, account: str, asset_code: str, interval: str, source: str,
                        *, net_qty: int, avg_price: float | None, realized_pnl: float,
                        last_price: float | None) -> None:
        pos = self.position(account, asset_code, interval, source)
        if pos is None:
            pos = FuturesPaperPosition(account=account, asset_code=asset_code, interval=interval,
                                       source=source)
            self.session.add(pos)
        pos.net_qty = net_qty
        pos.avg_price = avg_price
        pos.realized_pnl = realized_pnl
        pos.last_price = last_price
        pos.updated_at = datetime.now(UTC)

    def positions(self, account: str) -> list[FuturesPaperPosition]:
        return list(self.session.scalars(select(FuturesPaperPosition).where(
            FuturesPaperPosition.account == account).order_by(
            FuturesPaperPosition.asset_code, FuturesPaperPosition.source)))

    def log_trade(self, **kw) -> None:
        self.session.add(FuturesPaperTrade(**kw))

    def recent_trades(self, account: str, *, limit: int = 30) -> list[FuturesPaperTrade]:
        return list(self.session.scalars(select(FuturesPaperTrade).where(
            FuturesPaperTrade.account == account).order_by(
            FuturesPaperTrade.ts.desc()).limit(limit)))

    def closed_trades(self, account: str) -> list[FuturesPaperTrade]:
        """Любое ЗАКРЫТИЕ с реализованным P&L — основа win-rate/profit-factor трек-рекорда.

        Считаем по `realized_pnl IS NOT NULL` (ставится ТОЛЬКО на закрытии), а не по reason=='exit':
        при сессионной дисциплине почти все интрадей-сделки закрываются форс-флэтом к закрытию
        сессии (reason='session_flat'), и фильтр по 'exit' прятал бы их из метрик качества."""
        return list(self.session.scalars(select(FuturesPaperTrade).where(
            FuturesPaperTrade.account == account,
            FuturesPaperTrade.realized_pnl.isnot(None)).order_by(FuturesPaperTrade.ts)))

    def record_equity(self, account: str, ts: datetime, *, equity: float, realized_pnl: float,
                      unrealized_pnl: float, open_positions: int, peak_equity: float,
                      drawdown_pct: float, gross_margin: float) -> None:
        """Идемпотентно записать снимок эквити за час `ts` (upsert по (account, ts))."""
        stmt = (
            pg_insert(FuturesPaperEquity)
            .values(account=account, ts=ts, equity=equity, realized_pnl=realized_pnl,
                    unrealized_pnl=unrealized_pnl, open_positions=open_positions,
                    peak_equity=peak_equity, drawdown_pct=drawdown_pct, gross_margin=gross_margin)
            .on_conflict_do_update(
                index_elements=["account", "ts"],
                set_={"equity": equity, "realized_pnl": realized_pnl,
                      "unrealized_pnl": unrealized_pnl, "open_positions": open_positions,
                      "peak_equity": peak_equity, "drawdown_pct": drawdown_pct,
                      "gross_margin": gross_margin})
        )
        self.session.execute(stmt)

    def equity_curve(self, account: str, *, days: int = 365) -> list[FuturesPaperEquity]:
        """Снимки эквити по возрастанию времени (последние `days` точек-часов)."""
        rows = self.session.scalars(select(FuturesPaperEquity).where(
            FuturesPaperEquity.account == account).order_by(
            FuturesPaperEquity.ts.desc()).limit(days * 24)).all()
        return list(reversed(rows))

    def reset_account(self, account: str) -> dict[str, int]:
        """Чистый сброс БУМАЖНОГО СЧЁТА: позиции + снимки эквити + лог сделок счёта.

        НЕ трогает обучающий датасет (`futures_candles`/`futures_decisions`/`futures_model_runs`) —
        только состояние песочницы данного `account`. Возвращает счётчики удалённых строк. Маржа
        обнуляется (позиций нет) → транзиентный брутто-маржа-halt снимается сам. Идемпотентно."""
        out: dict[str, int] = {}
        for label, model in (("positions", FuturesPaperPosition),
                             ("equity", FuturesPaperEquity), ("trades", FuturesPaperTrade)):
            res = self.session.execute(delete(model).where(model.account == account))
            out[label] = res.rowcount or 0
        FuturesRiskStateRepository(self.session).set_state(account, halted=False, reason=None)
        return out


class FuturesRiskStateRepository:
    """Kill-switch бумажного счёта форка (Трек 2 / Пул 9 / B)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, account: str) -> FuturesRiskState | None:
        return self.session.get(FuturesRiskState, account)

    def is_halted(self, account: str) -> bool:
        st = self.get(account)
        return bool(st and st.halted)

    def set_state(self, account: str, *, halted: bool, reason: str | None,
                  resumed_at: datetime | None = None,
                  baseline_equity: float | None = None) -> None:
        """Идемпотентно выставить состояние kill-switch счёта (upsert по account)."""
        now = datetime.now(UTC)
        if not halted and resumed_at is None:
            resumed_at = now

        values = {
            "account": account,
            "halted": halted,
            "reason": reason,
            "updated_at": now,
        }
        set_dict = {
            "halted": halted,
            "reason": reason,
            "updated_at": now,
        }

        if resumed_at is not None:
            values["resumed_at"] = resumed_at
            set_dict["resumed_at"] = resumed_at
        if baseline_equity is not None:
            values["baseline_equity"] = baseline_equity
            set_dict["baseline_equity"] = baseline_equity

        stmt = (
            pg_insert(FuturesRiskState)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["account"],
                set_=set_dict)
        )
        self.session.execute(stmt)


class MarketRegimeRepository:
    """История режимов рынка во времени (G2/L5)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def replace_history(self, rows: list[tuple]) -> int:
        """Идемпотентно записать историю режимов: rows=[(day, state, label, vol)].

        Upsert по дню (HMM переразмечает историю при пересчёте). Возвращает число строк."""
        for day, state, label, vol in rows:
            stmt = (
                pg_insert(MarketRegime)
                .values(day=day, state=state, label=label, vol=vol)
                .on_conflict_do_update(
                    index_elements=["day"],
                    set_={"state": state, "label": label, "vol": vol})
            )
            self.session.execute(stmt)
        return len(rows)

    def latest(self) -> MarketRegime | None:
        return self.session.scalars(
            select(MarketRegime).order_by(MarketRegime.day.desc()).limit(1)
        ).first()

    def series(self, *, days: int = 180) -> list[MarketRegime]:
        """Последние `days` дней истории режимов по возрастанию даты."""
        rows = self.session.scalars(
            select(MarketRegime).order_by(MarketRegime.day.desc()).limit(days)
        ).all()
        return list(reversed(rows))


class CompanyRepository:
    """Профиль эмитента (L2: состав компании)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def by_id(self, company_id: int) -> Company | None:
        return self.session.get(Company, company_id)

    def update_profile(self, company_id: int, *, description: str | None = None,
                       market_cap: float | None = None, free_float: float | None = None,
                       shares: float | None = None) -> Company | None:
        """Обновить снапшот-профиль компании. Переданные None-поля не трогаем (частичный апдейт)."""
        company = self.session.get(Company, company_id)
        if company is None:
            return None
        if description is not None:
            company.description = description
        if market_cap is not None:
            company.market_cap = market_cap
        if free_float is not None:
            company.free_float = free_float
        if shares is not None:
            company.shares = shares
        self.session.flush()
        return company


class RevenueSegmentRepository:
    """Сегменты выручки эмитента (L2: «из чего складывается выручка»)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, company_id: int, segment: str, value: float, *,
               share: float | None = None, period: str | None = None,
               source: str = "manual") -> RevenueSegment:
        """Записать/перезаписать сегмент за (компания, сегмент, период, источник) — идемпотентно."""
        row = self.session.scalars(
            select(RevenueSegment).where(
                RevenueSegment.company_id == company_id,
                RevenueSegment.segment == segment,
                RevenueSegment.period.is_(period) if period is None
                else RevenueSegment.period == period,
                RevenueSegment.source == source,
            )
        ).first()
        if row is None:
            row = RevenueSegment(company_id=company_id, segment=segment, value=value,
                                 share=share, period=period, source=source)
            self.session.add(row)
        else:
            row.value, row.share = value, share
        self.session.flush()
        return row

    def for_company(self, company_id: int) -> list[RevenueSegment]:
        """Сегменты компании за свежайший доступный период (по убыванию доли/выручки)."""
        rows = self.session.scalars(
            select(RevenueSegment).where(RevenueSegment.company_id == company_id)
            .order_by(RevenueSegment.period.desc().nulls_last())
        ).all()
        if not rows:
            return []
        latest_period = rows[0].period
        same = [r for r in rows if r.period == latest_period]
        same.sort(key=lambda r: (r.share if r.share is not None else r.value), reverse=True)
        return same


class UserRepository:
    """Пользователи бота (5b): идентичность Telegram и авторизация."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def register(self, telegram_user_id: int, chat_id: str,
                 username: str | None = None) -> User:
        """Upsert по telegram_user_id: создаёт нового (allowed=false) или обновляет chat_id/имя.

        Возврат — актуальная запись. Авторизацию (`allowed`) меняет только `set_allowed`/bootstrap.
        """
        user = self.session.scalars(
            select(User).where(User.telegram_user_id == telegram_user_id)
        ).first()
        if user is None:
            user = User(telegram_user_id=telegram_user_id, chat_id=str(chat_id),
                        username=username)
            self.session.add(user)
        else:
            user.chat_id = str(chat_id)
            if username:
                user.username = username
        self.session.flush()
        return user

    def get_by_chat_id(self, chat_id: str) -> User | None:
        return self.session.scalars(
            select(User).where(User.chat_id == str(chat_id))
        ).first()

    def list_allowed(self) -> list[User]:
        return list(self.session.scalars(
            select(User).where(User.allowed.is_(True)).order_by(User.id)
        ))

    def list_all(self) -> list[User]:
        """Все пользователи (для админ-обзора в боте): сначала ожидающие, потом по id."""
        return list(self.session.scalars(
            select(User).order_by(User.allowed, User.id)
        ))

    def set_allowed(self, telegram_user_id: int, allowed: bool,
                    *, role: str | None = None) -> User | None:
        """Сменить авторизацию (и опц. роль). None — пользователя нет."""
        user = self.session.scalars(
            select(User).where(User.telegram_user_id == telegram_user_id)
        ).first()
        if user is None:
            return None
        user.allowed = allowed
        if role is not None:
            user.role = role
        self.session.flush()
        return user

    def ensure_admin(self, telegram_user_id: int, chat_id: str) -> User:
        """Гарантировать разрешённого admin'а (bootstrap из стартового allowlist настроек)."""
        user = self.register(telegram_user_id, chat_id)
        user.allowed = True
        user.role = "admin"
        self.session.flush()
        return user


class ForecastRepository:
    """Прогнозы брокеров (F10): целевая цена/дивиденд/ставка по (статья, актив)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_forecast(self, *, article_id: int, asset_id: int, kind: str, value: float,
                     unit: str, target_date=None, source_channel: str | None = None) -> int:
        """Идемпотентно вставляет прогноз (ON CONFLICT DO NOTHING по uq_forecast).

        Возвращает число добавленных строк (0 — уже был). RETURNING даёт надёжный счётчик
        (rowcount при DO NOTHING без RETURNING на этом драйвере = -1)."""
        rows = self.session.execute(
            pg_insert(Forecast)
            .values(article_id=article_id, asset_id=asset_id, kind=kind, value=value,
                    unit=unit, target_date=target_date, source_channel=source_channel)
            .on_conflict_do_nothing(constraint="uq_forecast")
            .returning(Forecast.id)
        ).fetchall()
        return len(rows)

    def list_forecasts(self, *, asset_id: int | None = None,
                       limit: int = 50) -> list[tuple[Forecast, Asset]]:
        """Прогнозы с активом, новые сверху. asset_id — фильтр по активу."""
        stmt = (
            select(Forecast, Asset)
            .join(Asset, Forecast.asset_id == Asset.id)
            .order_by(Forecast.created_at.desc(), Forecast.id.desc())
            .limit(limit)
        )
        if asset_id is not None:
            stmt = stmt.where(Forecast.asset_id == asset_id)
        return [(f, a) for f, a in self.session.execute(stmt)]
