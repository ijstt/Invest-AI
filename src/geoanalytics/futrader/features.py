"""Трек 2 / Фаза A: контекст-признаки ЭДЖ из аналитики Трека 1.

КЛЮЧЕВОЕ ОТЛИЧИЕ форка: generic-TA-бот эджа не имеет, а у нас готов рыночно-аналитический слой —
переиспользуем его как признаки решения. На вход модели T2.4, кроме TA, идут: режим рынка (L5,
`market_regimes`), индекс настроения (B1, `market_sentiment` market-scope) и кросс-актив
(Brent/USD/IMOEX побарные доходности). Это рыночно-ГЛОБАЛЬНЫЙ контекст (одинаков для всех
инструментов), поэтому грузим историю ОДИН раз в `EdgeContext` и джойним as-of дате бара
(последний день ≤ дате). Инструмент кодируется отдельно (`instr`) на уровне лога решений.

Грациозно: если данные Трека 1 за дату отсутствуют (ранняя история/пустые таблицы) — признак
просто отсутствует (модель ест NaN). Приватные лоадеры `correlations` переиспользуем (свой пакет).
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date, datetime

from geoanalytics.core.logging import get_logger

log = get_logger("futrader.features")

# Инструмент как признак для ПУЛИНГА (одна модель на все активы, инструмент различает их).
INSTRUMENT_CODES = {"BR": 0, "GOLD": 1, "Si": 2, "Eu": 3, "CNY": 4, "RTS": 5}

# Ключи признаков-эдж (расширяют TA-признаки решения). dir/instr добавляются отдельно.
EDGE_KEYS = ("regime_state", "regime_vol", "sent_ewma", "sent_breadth",
             "brent_ret", "usd_ret", "imoex_ret")
# Per-instrument news-эдж (Tier B/Фаза D): сентимент базового актива фьючерса (а не рыночно-
# глобальный скаляр). Активен там, где базовый — Asset с новостями: RTS→IMOEX (индекс), BR (нефть).
# У сырья/FX (GOLD/Si/Eu/CNY) per-asset новостей обычно нет → признак NaN (модель опускает),
# но инфраструктура корректна и активируется по мере роста покрытия новостями.
ASSET_NEWS_KEYS = ("asset_sent_ewma", "asset_sent_breadth")


def _asof(days: list[date], values: list, d: date, *, strict: bool = False):
    """Значение последнего дня ≤ d (или СТРОГО < d при strict=True — анти-lookahead для интрадей).

    strict=True: дневные агрегаты дня d (режим/сентимент/доходность ПО ЗАКРЫТИЮ) НЕ известны
    внутридневному бару дня d → берём последний полностью завершённый день (D−1). Для дневного
    бара (strict=False) день d контемпорален его закрытию — берём d (forward-fill разрежённых)."""
    pos = bisect_left(days, d) if strict else bisect_right(days, d)
    return values[pos - 1] if pos else None


class EdgeContext:
    """Загруженная рыночно-глобальная история Трека 1 для as-of-дата джойна к барам фьючерса."""

    def __init__(self, session, *, lookback_days: int = 500) -> None:
        self._regime_days: list[date] = []
        self._regime: list[tuple[int, float | None]] = []
        self._sent_days: list[date] = []
        self._sent: list[tuple[float, float]] = []
        self._ret_days: dict[str, list[date]] = {}
        self._ret_val: dict[str, list[float]] = {}
        # Per-instrument news-сентимент базового актива: asset_code → (days, [(ewma, breadth)]).
        self._asent_days: dict[str, list[date]] = {}
        self._asent: dict[str, list[tuple[float, float]]] = {}
        self._load(session, lookback_days)
        self._load_asset_sentiment(session, lookback_days)

    def _load(self, session, lookback_days: int) -> None:
        from geoanalytics.analytics import market_sentiment as ms
        from geoanalytics.analytics.correlations import (
            _fx_levels,
            _macro_levels,
            _price_levels,
            _returns_by_date,
        )
        from geoanalytics.storage.models import Asset
        from geoanalytics.storage.repositories import MarketRegimeRepository

        try:
            regimes = MarketRegimeRepository(session).series(days=lookback_days)
            for r in regimes:
                self._regime_days.append(r.day)
                self._regime.append((r.state, r.vol))
        except Exception as exc:  # noqa: BLE001 — отсутствие данных не валит признаки
            log.warning("edge_regime_load_failed", error=str(exc))

        try:
            for s in ms.series(session, "market", days=lookback_days):
                self._sent_days.append(s.day)
                self._sent.append((s.sent_ewma, s.breadth))
        except Exception as exc:  # noqa: BLE001
            log.warning("edge_sentiment_load_failed", error=str(exc))

        # Кросс-актив: побарные доходности непрерывных рядов (Brent/USD/IMOEX).
        try:
            self._add_returns("brent_ret", _returns_by_date(_macro_levels(session, "brent")))
        except Exception as exc:  # noqa: BLE001
            log.warning("edge_brent_load_failed", error=str(exc))
        try:
            self._add_returns("usd_ret", _returns_by_date(_fx_levels(session, "USD")))
        except Exception as exc:  # noqa: BLE001
            log.warning("edge_usd_load_failed", error=str(exc))
        try:
            imoex = session.query(Asset).filter(Asset.ticker == "IMOEX").first()
            if imoex is not None:
                self._add_returns("imoex_ret", _returns_by_date(_price_levels(session, imoex.id)))
        except Exception as exc:  # noqa: BLE001
            log.warning("edge_imoex_load_failed", error=str(exc))

    def _add_returns(self, key: str, rets: dict[date, float]) -> None:
        days = sorted(rets)
        self._ret_days[key] = days
        self._ret_val[key] = [rets[d] for d in days]

    def _load_asset_sentiment(self, session, lookback_days: int) -> None:
        """Грузим asset-scope сентимент базового актива каждого фьючерса (Tier B/Фаза D).

        Базовый-новостной актив: для индексных фьючерсов — индекс (RTS→IMOEX), иначе пробуем сам
        тикер фьючерса как Asset (BR — нефть как актив). Сырьё/FX без новостного Asset пропускаем —
        признак останется NaN. Грациозно: пустые/сбойные источники не валят остальные признаки."""
        from geoanalytics.analytics import market_sentiment as ms
        from geoanalytics.futrader.underlying import resolve_underlying
        from geoanalytics.storage.models import Asset

        for code in INSTRUMENT_CODES:
            u = resolve_underlying(code)
            ticker = u[1] if u and u[0] == "index" else code   # индекс→его тикер; иначе тикер фьюча
            try:
                asset = session.query(Asset).filter(Asset.ticker == ticker).first()
                if asset is None:
                    continue
                rows = ms.series(session, "asset", asset_id=asset.id, days=lookback_days)
                if not rows:
                    continue
                self._asent_days[code] = [s.day for s in rows]
                self._asent[code] = [(s.sent_ewma, s.breadth) for s in rows]
            except Exception as exc:  # noqa: BLE001 — один актив не валит остальные
                log.warning("edge_asset_sentiment_load_failed", code=code, error=str(exc))

    def asset_features_at(self, ts: datetime, asset_code: str, *, intraday: bool = True) -> dict:
        """News-сентимент базового актива as-of дате бара (анти-lookahead D−1 для интрадей). Пусто,
        если у инструмента нет новостного базового актива (сырьё/FX) — модель опускает признак."""
        days = self._asent_days.get(asset_code)
        if not days:
            return {}
        v = _asof(days, self._asent[asset_code], ts.date(), strict=intraday)
        if v is None:
            return {}
        return {"asset_sent_ewma": round(float(v[0]), 4),
                "asset_sent_breadth": round(float(v[1]), 4)}

    def features_at(self, ts: datetime, *, intraday: bool = True) -> dict:
        """Признаки-эдж as-of дате бара (анти-lookahead). intraday=True → дневные фичи берём СТРОГО
        за D−1 (внутридневной бар не знает дневной агрегат своего дня — устраняет same-day утечку и
        train/serve skew); intraday=False (дневной бар) → день d. Пустые источники опускаются."""
        d = ts.date()
        out: dict[str, float] = {}
        reg = _asof(self._regime_days, self._regime, d, strict=intraday)
        if reg is not None:
            out["regime_state"] = float(reg[0])
            if reg[1] is not None:
                out["regime_vol"] = round(float(reg[1]), 4)
        sent = _asof(self._sent_days, self._sent, d, strict=intraday)
        if sent is not None:
            out["sent_ewma"] = round(float(sent[0]), 4)
            out["sent_breadth"] = round(float(sent[1]), 4)
        for key in ("brent_ret", "usd_ret", "imoex_ret"):
            days = self._ret_days.get(key)
            if days:
                v = _asof(days, self._ret_val[key], d, strict=intraday)
                if v is not None:
                    out[key] = round(float(v) * 100, 4)   # в процентах, как TA-доходности
        return out
