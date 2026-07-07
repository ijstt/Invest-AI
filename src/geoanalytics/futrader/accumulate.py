"""Трек 2 / Фаза 0: накопление пулинг-датасета фьючерсного форка.

Узкое место Трека 2 — ГЛУБИНА ДАННЫХ, а не модель (десятки решений = ноль статистической мощности).
Эта точка собирает МАКС истории по ВСЕМ инструментам FORTS × интервалам × стратегиям в единый
размеченный датасет `futures_decisions` (пулинг: одна модель учится на всех инструментах, инструмент
и направление — признаки). Гоняется из CLI (`geo futures-intraday accumulate`) и ежедневным джобом
scheduler (офф-пик). Чистый numeric — НЕ конкурирует за Ollama/GPU и не трогает LLM-замок.

Идемпотентно (бэкфилл upsert по свече, лог решений upsert по точке) и сбой-изолировано: падение
одного инструмента/стратегии (сеть/ISS) не валит весь проход. Глубина окна — по интервалу
(`INTERVAL_DAYS`): минутка/10м у ISS лишь за недавнее окно, час/день — длинная история.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from geoanalytics.core.logging import get_logger
from geoanalytics.futrader.data import INTERVAL_CODES, backfill_futures_intraday
from geoanalytics.futrader.decisions import (
    SIGNAL_FNS,
    log_cross_sectional_decisions,
    log_decisions,
)
from geoanalytics.futrader.features import EdgeContext

log = get_logger("futrader.accumulate")

# Инструменты форка (тикеры → asset_code в seed.FUTURES).
DEFAULT_TICKERS = ("BR", "GD", "SI", "EU", "CNY", "RTS")
# Интервалы: 10m — ИНТРАДЕЙ-таймфрейм Фазы B (нативный ISS, ~45д истории + роллинг вперёд — стартуем
# накопление СЕЙЧАС, чтобы к Фазе B была глубина); час/день — длинная история под контекст/оценку.
DEFAULT_INTERVALS = ("10m", "1h", "1d")
DEFAULT_STRATEGIES = tuple(SIGNAL_FNS)

# Глубина окна бэкфилла по интервалу (дни). Контракт живёт <1 года, так что 400д дневных покрывают
# его целиком; час — 180д (тяжелее по объёму бар); минутка/10м — недавнее окно ISS.
INTERVAL_DAYS = {"1m": 7, "10m": 45, "1h": 180, "1d": 400}


@dataclass
class AccumStat:
    """Сводка по одному (инструмент, интервал): новых свечей, записано/размечено решений."""

    ticker: str
    interval: str
    candles: int = 0
    decisions: int = 0
    labeled: int = 0


@dataclass
class AccumResult:
    stats: list[AccumStat] = field(default_factory=list)

    @property
    def candles(self) -> int:
        return sum(s.candles for s in self.stats)

    @property
    def decisions(self) -> int:
        return sum(s.decisions for s in self.stats)

    @property
    def labeled(self) -> int:
        return sum(s.labeled for s in self.stats)


def accumulate_dataset(session, *, tickers=DEFAULT_TICKERS, intervals=DEFAULT_INTERVALS,
                       strategies=DEFAULT_STRATEGIES, days: int | None = None,
                       horizon_bars: int = 12, max_contracts: int = 6) -> AccumResult:
    """Бэкфилл всех инструментов × интервалов + лог решений всех стратегий → пулинг-датасет.

    `days=None` — глубина окна по интервалу (`INTERVAL_DAYS`); иначе единое окно для всех.
    Идемпотентно. Сбой одного инструмента (бэкфилл) пропускает его стратегии, но не валит проход;
    сбой одной стратегии не валит остальные. Возвращает посводную статистику.
    """
    # Признаки-эдж рыночно-глобальны (режим/сентимент/кросс-актив) — грузим ОДИН раз на весь проход.
    edge = EdgeContext(session)
    result = AccumResult()
    for ticker in tickers:
        for interval in intervals:
            if interval not in INTERVAL_CODES:
                log.warning("accumulate_unknown_interval", interval=interval)
                continue
            window = days if days is not None else INTERVAL_DAYS.get(interval, 60)
            stat = AccumStat(ticker=ticker, interval=interval)
            try:
                stat.candles = backfill_futures_intraday(
                    session, ticker, interval=interval, days=window,
                    max_contracts=max_contracts)
            except Exception as exc:  # noqa: BLE001 — сеть/ISS не валит весь проход
                log.warning("accumulate_backfill_failed", ticker=ticker,
                            interval=interval, error=str(exc))
                result.stats.append(stat)
                continue
            for strat in strategies:
                try:
                    r = log_decisions(session, ticker, interval=interval, source=strat,
                                      horizon_bars=horizon_bars, edge=edge)
                    stat.decisions += r.stored
                    stat.labeled += r.labeled
                except Exception as exc:  # noqa: BLE001 — одна стратегия не валит остальные
                    log.warning("accumulate_log_failed", ticker=ticker, interval=interval,
                                strategy=strat, error=str(exc))
            result.stats.append(stat)

    # Кросс-секционная стратегия (Пул 9/E): один лог на интервал (нужны ВСЕ инструменты выровнены).
    for interval in intervals:
        if interval not in INTERVAL_CODES:
            continue
        xstat = AccumStat(ticker="XSEC", interval=interval)
        try:
            r = log_cross_sectional_decisions(session, tickers=tickers, interval=interval,
                                              horizon_bars=horizon_bars, edge=edge)
            xstat.decisions = r.stored
            xstat.labeled = r.labeled
        except Exception as exc:  # noqa: BLE001 — кросс-секция не валит проход
            log.warning("accumulate_xsec_failed", interval=interval, error=str(exc))
        result.stats.append(xstat)

    log.info("accumulate_done", instruments=len(tickers), intervals=len(intervals),
             strategies=len(strategies), candles=result.candles,
             decisions=result.decisions, labeled=result.labeled)
    return result
