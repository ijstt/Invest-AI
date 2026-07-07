"""C1: движок рекомендаций «стойка + сигнал + обоснование» (AssetStance).

Сводит уже считающиеся, но разрозненные сигналы (тональный моментум B1, техническая картина
B2, потенциал к таргетам брокеров B3) в ОДНУ объяснимую стойку по активу: сигнал
buy/accumulate/hold/reduce/sell, уверенность (conviction) и список драйверов с явным знаком.
Без размера позиции — это образовательная аналитика, не индивидуальная инвестрекомендация.

Методология. Каждый драйвер даёт знаковый вклад в [−1, +1] (── медвежий … ++ бычий) с
прозрачным весом-константой. Композитный балл = взвешенное среднее ДОСТУПНЫХ драйверов (нет
данных — драйвер не штрафует, а исключается). Балл → сигнал по порогам. Уверенность растёт с
|баллом|, согласием драйверов по знаку и полнотой данных; робастность бэктеста (C3) её
модулирует (сильная вне-выборки стратегия ↑ доверие к технической картине, слабая ↓).

Ядро (`compose_stance` и знаковые функции) — чистое, тестируется без БД; обёртка
`stance_for_asset` собирает входы из БД.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Прозрачные веса знаковых драйверов (нормируем по доступным — сумма не обязана быть 1).
WEIGHTS = {"sentiment": 0.28, "technical": 0.30, "forecast": 0.22, "fundamental": 0.20}

# Профили весов по горизонту: long делает упор на фундаментал/качество (долгосрочное
# накопление), swing (по умолчанию) — сбалансирован с упором на тех.картину/настроение.
_HORIZON_WEIGHTS = {
    "swing": WEIGHTS,
    "long": {"sentiment": 0.12, "technical": 0.13, "forecast": 0.20, "fundamental": 0.55},
}

# Пороги балла → сигнал (симметричны относительно нуля).
_BUY, _ACC, _RED, _SELL = 0.45, 0.15, -0.15, -0.45

_LABEL = {
    "buy": "Покупать", "accumulate": "Набирать", "hold": "Держать",
    "reduce": "Сокращать", "sell": "Продавать",
}


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return min(max(x, lo), hi)


@dataclass
class Driver:
    """Один знаковый вклад в стойку: метка, нормированный вклад [−1,1], вес, пояснение."""

    key: str
    label: str
    contribution: float          # знаковый, [−1, +1]
    weight: float
    detail: str = ""

    @property
    def sign(self) -> int:
        if self.contribution > 0.05:
            return 1
        if self.contribution < -0.05:
            return -1
        return 0


@dataclass
class AssetStance:
    """Итоговая стойка по активу: сигнал + уверенность + драйверы + риск."""

    ticker: str
    signal: str                  # buy | accumulate | hold | reduce | sell
    score: float                 # композитный балл [−1, +1]
    conviction: float            # уверенность [0, 1]
    drivers: list[Driver] = field(default_factory=list)
    risk: dict = field(default_factory=dict)
    note: str = ""

    @property
    def label(self) -> str:
        return _LABEL.get(self.signal, self.signal)

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker, "signal": self.signal, "label": self.label,
            "score": round(self.score, 3), "conviction": round(self.conviction, 3),
            "drivers": [
                {"key": d.key, "label": d.label, "sign": d.sign,
                 "contribution": round(d.contribution, 3), "detail": d.detail}
                for d in self.drivers
            ],
            "risk": self.risk, "note": self.note,
        }


# --------------------------------------------------------------------------- #
# Знаковые драйверы (чистые функции; None — сигнал недоступен, драйвер пропускается).
# --------------------------------------------------------------------------- #
def sentiment_driver(sent_ewma: float | None, breadth: float | None) -> Driver | None:
    """Тональный моментум B1: EWMA настроения (масштаб ~0.3 = сильный) + ширина (доля ±)."""
    if sent_ewma is None and breadth is None:
        return None
    ewma_n = _clamp((sent_ewma or 0.0) / 0.3)
    breadth_n = _clamp(breadth or 0.0)
    contrib = 0.6 * ewma_n + 0.4 * breadth_n
    detail = f"моментум {(sent_ewma or 0.0):+.2f}, ширина {(breadth or 0.0):+.2f}"
    return Driver("sentiment", "Настроение", contrib, WEIGHTS["sentiment"], detail)


def technical_driver(ind: dict) -> Driver | None:
    """Техническая картина B2: тренд (цена/SMA50, golden/death-крест) + MACD + RSI-истощение."""
    last = ind.get("last")
    parts: list[float] = []
    bits: list[str] = []
    sma50 = ind.get("sma50")
    if last and sma50:
        parts.append(_clamp((last / sma50 - 1.0) / 0.05))   # ±5% к SMA50 = полный вклад
        bits.append("над SMA50" if last >= sma50 else "под SMA50")
    sma200 = ind.get("sma200")
    if sma50 and sma200:
        parts.append(0.5 if sma50 > sma200 else -0.5)        # golden/death cross
    hist = ind.get("macd_hist")
    if hist is not None and last:
        parts.append(_clamp(hist / (0.015 * abs(last))))
        bits.append("MACD↑" if hist > 0 else "MACD↓")
    rsi = ind.get("rsi14")
    if rsi is not None:                                       # истощение (мягкий контр-вклад)
        if rsi > 70:
            parts.append(-0.3)
            bits.append(f"RSI {rsi:.0f} перекуплен")
        elif rsi < 30:
            parts.append(0.3)
            bits.append(f"RSI {rsi:.0f} перепродан")
    if not parts:
        return None
    contrib = _clamp(sum(parts) / len(parts))
    return Driver("technical", "Теханализ", contrib, WEIGHTS["technical"], ", ".join(bits))


def forecast_driver(forecasts: list[dict]) -> Driver | None:
    """Прогнозы брокеров B3: средний потенциал к таргету (±20% = полный вклад)."""
    implied = [f["implied_pct"] for f in forecasts
               if f.get("kind") == "target_price" and not f.get("matured")
               and f.get("implied_pct") is not None]
    if not implied:
        return None
    avg = sum(implied) / len(implied)
    contrib = _clamp(avg / 20.0)
    return Driver("forecast", "Прогнозы брокеров", contrib, WEIGHTS["forecast"],
                  f"средний потенциал {avg:+.1f}% ({len(implied)} таргет(ов))")


def fundamental_driver(quality: dict | None, fair_value: dict | None) -> Driver | None:
    """L4 фундаментальный драйвер: дёшево+качественно → бычий, дорого+слабо → медвежий.

    Вклад = относительная оценка к сектору (апсайд ±30% = полный) ⊕ наклон качества (вердикт
    ok/avoid → ±). Только качество (нет мультипликаторов) — приглушённый вклад от качества."""
    if not quality and not fair_value:
        return None
    bits: list[str] = []
    val_n = 0.0
    if fair_value is not None:
        val_n = _clamp(fair_value["upside_pct"] / 30.0)
        bits.append(f"{fair_value['verdict']} {fair_value['upside_pct']:+.0f}% к сектору")
    q_tilt = 0.0
    if quality is not None:
        q_tilt = 2 * quality["score"] - 1.0           # [0,1] → [−1,1]
        if quality["positives"]:
            bits.append(", ".join(quality["positives"][:2]))
        if quality["flags"]:
            bits.append("⚠ " + ", ".join(quality["flags"][:2]))
    if fair_value is not None and quality is not None:
        contrib = _clamp(0.6 * val_n + 0.4 * q_tilt)
    elif fair_value is not None:
        contrib = _clamp(val_n)
    else:
        contrib = _clamp(0.5 * q_tilt)                # только качество — приглушаем
    return Driver("fundamental", "Фундаментал", contrib, WEIGHTS["fundamental"],
                  "; ".join(bits))


def apply_quality_gate(stance: AssetStance, quality: dict | None) -> AssetStance:
    """Квалити-гейт: слабый фундаментал (verdict=avoid) не даёт бычьего сигнала на вход.

    «Понимать, когда компания плоха, чтобы вовсе не входить»: buy/accumulate → hold."""
    if quality and quality["verdict"] == "avoid" and stance.signal in ("buy", "accumulate"):
        stance.signal = "hold"
        gate = "квалити-гейт: слабый фундаментал — вход не рекомендуется"
        stance.note = f"{stance.note} {gate}".strip()
    return stance


def signal_from_score(score: float) -> str:
    """Балл [−1,1] → дискретный сигнал по симметричным порогам."""
    if score >= _BUY:
        return "buy"
    if score >= _ACC:
        return "accumulate"
    if score <= _SELL:
        return "sell"
    if score <= _RED:
        return "reduce"
    return "hold"


def _conviction(score: float, drivers: list[Driver], n_possible: int,
                backtest_factor: float) -> float:
    """Уверенность из |балла|, согласия драйверов по знаку, полноты данных и бэктеста.

    `backtest_factor` ∈ [~0.8, ~1.2] — модулятор робастности стратегии (C3): >1 при сильной
    вне-выборки технической стратегии, <1 при слабой; 1.0 — нейтрально/нет данных.
    """
    signed = [d for d in drivers if d.sign != 0]
    if not signed:
        return 0.0
    sgn = 1 if score > 0 else -1 if score < 0 else 0
    agree = sum(1 for d in signed if d.sign == sgn) / len(signed) if sgn else 0.5
    completeness = min(len(signed) / max(n_possible, 1), 1.0)
    base = abs(score) * (0.5 + 0.5 * agree) * (0.6 + 0.4 * completeness)
    return round(min(base * backtest_factor, 1.0), 3)


def compose_stance(ticker: str, drivers: list[Driver | None], *,
                   n_possible: int = 3, backtest_factor: float = 1.0,
                   risk: dict | None = None, note: str = "") -> AssetStance:
    """Чистое ядро: список знаковых драйверов → стойка (балл/сигнал/уверенность).

    Балл = взвешенное среднее доступных драйверов; None-драйверы исключаются (не штрафуют).
    """
    present = [d for d in drivers if d is not None]
    num = den = 0.0
    for d in present:
        num += d.weight * d.contribution
        den += d.weight
    score = _clamp(num / den) if den else 0.0
    signal = signal_from_score(score)
    conviction = _conviction(score, present, n_possible, backtest_factor)
    # Драйверы сортируем по |вкладу| (главное — вперёд).
    present.sort(key=lambda d: abs(d.contribution), reverse=True)
    return AssetStance(ticker=ticker.upper(), signal=signal, score=score,
                       conviction=conviction, drivers=present, risk=risk or {}, note=note)


def _backtest_factor(bt) -> float:
    """Модулятор уверенности из бэктеста (C3): робастная стратегия ↑, слабая ↓ доверие.

    Использует OOS-эффективность walk-forward, если передан `WalkForwardResult` (поле
    `efficiency`); иначе — Шарп простого бэктеста (`BacktestResult.sharpe`). Нет данных → 1.0.
    """
    if bt is None:
        return 1.0
    eff = getattr(bt, "efficiency", None)
    if eff is not None:                       # walk-forward OOS (честнее)
        if eff >= 0.7:
            return 1.18
        if eff <= 0.2:
            return 0.85
        return 1.0
    sharpe = getattr(bt, "sharpe", None)
    if sharpe is None:
        return 1.0
    if sharpe >= 0.8:
        return 1.12
    if sharpe <= 0.0:
        return 0.88
    return 1.0


@dataclass
class PortfolioStance:
    """Среднесрочная сводка-стойка по портфелю: агрегат позиционных стоек, взвешенный по весам."""

    period: str = "W"
    # бычья / умеренно-бычья / нейтральная / умеренно-медвежья / медвежья / смешанная
    posture: str = "нет данных"
    score: float = 0.0               # взвешенный по весам средний балл [−1,1]
    conviction: float = 0.0
    n_bullish: int = 0
    n_neutral: int = 0
    n_bearish: int = 0
    positions: list[dict] = field(default_factory=list)   # по позициям: сигнал/балл/вклад/драйвер
    leaders_pos: list[str] = field(default_factory=list)  # тикеры, тянущие портфель вверх
    leaders_neg: list[str] = field(default_factory=list)
    trend_1m_pct: float | None = None    # доходность портфеля ~1м (по value_series)
    trend_3m_pct: float | None = None    # ~3м
    note: str = ""


def _posture(score: float, n_bull: int, n_bear: int, n_total: int) -> str:
    """Слова стойки портфеля из балла и разброса сигналов позиций."""
    if n_total == 0:
        return "нет данных"
    if n_bull and n_bear and min(n_bull, n_bear) / n_total >= 0.3 and abs(score) < 0.2:
        return "смешанная"
    if score >= 0.3:
        return "бычья"
    if score >= _ACC:
        return "умеренно-бычья"
    if score <= -0.3:
        return "медвежья"
    if score <= _RED:
        return "умеренно-медвежья"
    return "нейтральная"


def _series_change_pct(series: list[tuple], days: int) -> float | None:
    """Изменение ряда (date, value) за последние `days` дней (база — точка на/до отсечки)."""
    from datetime import timedelta

    if len(series) < 2:
        return None
    last_date, last_val = series[-1]
    target = last_date - timedelta(days=days)
    base = series[0][1]
    for d, v in series:
        if d <= target:
            base = v
        else:
            break
    return round((last_val / base - 1) * 100, 2) if base else None


def portfolio_stance(session, report, *, period: str = "W") -> PortfolioStance:
    """C1+: среднесрочная (по умолчанию недельный ТФ) сводка-стойка по портфелю.

    Считает позиционную `AssetStance` на ТФ `period` для каждой неденежной позиции с историей,
    агрегирует балл/уверенность с весами позиций, классифицирует общую стойку, выделяет
    тянущие вверх/вниз тикеры и среднесрочный тренд стоимости (1м/3м из `report.value_series`).
    Бэктест в позиционных стойках отключён (пакетный расчёт — десяток прогонов на запрос дорог).
    """
    from sqlalchemy import select

    from geoanalytics.analytics.prices import asset_indicators
    from geoanalytics.storage.models import Asset

    rows: list[dict] = []
    num = den = conv_sum = 0.0
    n_bull = n_neut = n_bear = 0
    for p in report.positions:
        if p.weight_pct is None or p.note:        # денежные/без-цены строки несут note
            continue
        asset = session.scalars(select(Asset).where(Asset.ticker == p.ticker)).first()
        if asset is None or asset.kind in ("fund", "index"):   # MMF/индекс — без TA-стойки
            continue
        ind = asset_indicators(session, asset.id, period=period).as_dict()
        if not ind:
            continue
        st = stance_for_asset(session, asset.id, p.ticker, indicators=ind,
                              with_backtest=False, with_fundamentals=False, period=period)
        w = p.weight_pct / 100.0
        num += w * st.score
        den += w
        conv_sum += w * st.conviction
        if st.score > _ACC:
            n_bull += 1
        elif st.score < _RED:
            n_bear += 1
        else:
            n_neut += 1
        rows.append({
            "ticker": p.ticker, "weight_pct": p.weight_pct, "signal": st.signal,
            "label": st.label, "score": round(st.score, 3), "conviction": st.conviction,
            "contribution": round(w * st.score, 4),
            "top_driver": st.drivers[0].label if st.drivers else "",
        })

    score = num / den if den else 0.0
    rows.sort(key=lambda r: r["contribution"], reverse=True)
    out = PortfolioStance(
        period=period, posture=_posture(score, n_bull, n_bear, len(rows)),
        score=round(score, 3), conviction=round(conv_sum / den, 3) if den else 0.0,
        n_bullish=n_bull, n_neutral=n_neut, n_bearish=n_bear, positions=rows,
        leaders_pos=[r["ticker"] for r in rows if r["contribution"] > 0][:3],
        leaders_neg=[r["ticker"] for r in reversed(rows) if r["contribution"] < 0][:3],
        trend_1m_pct=_series_change_pct(report.value_series, 30),
        trend_3m_pct=_series_change_pct(report.value_series, 90),
    )
    if not rows:
        out.note = "Нет позиций с ценовой историей для среднесрочной оценки."
    return out


def directional_precision(pairs: list[tuple[float, float | None]], *,
                          score_eps: float = 0.15, move_pct: float = 1.0) -> dict:
    """Калибровка C1: доля совпадений знака стойки с реализованным движением рынка.

    `pairs` — (балл стойки, фактическая аномальная доходность abn_Nd %). Учитываются только
    направленные стойки (|балл| ≥ `score_eps`) против реально двинувшихся бумаг (|abn| ≥
    `move_pct`). precision = доля верно угаданного направления. Чистое ядро для встраивания в
    `continuous_eval`, когда дозреют `news_outcomes` (сейчас данных мало — прогон не форсируем).
    """
    n = correct = 0
    for score, abn in pairs:
        if abn is None or abs(score) < score_eps or abs(abn) < move_pct:
            continue
        n += 1
        if (score > 0) == (abn > 0):
            correct += 1
    return {"n": n, "correct": correct, "precision": (correct / n) if n else None}


def stance_for_asset(session, asset_id: int, ticker: str, *,
                     indicators: dict | None = None,
                     forecasts: list[dict] | None = None,
                     backtest=None, with_backtest: bool = True,
                     period: str = "D", horizon: str = "swing",
                     fundamentals: dict | None = None, with_fundamentals: bool = True,
                     var_contribution_rub: float | None = None) -> AssetStance:
    """Собирает входы стойки из БД и считает `AssetStance` по активу.

    `indicators`/`forecasts`/`backtest`/`fundamentals` можно передать заранее (карточка их уже
    считает — не дублируем). Иначе тянем из БД. `horizon` ("swing"/"long") выбирает профиль весов
    драйверов — long делает упор на фундаментал/качество. `with_fundamentals=False` отключает
    фундаментальный драйвер и квалити-гейт (для пакетного расчёта по портфелю — дорого).
    Риск собирается из дисперсии настроения, дивергенции, просадки/волатильности и вклада в VaR.
    """
    from geoanalytics.analytics.forecasts import forecasts_for_asset
    from geoanalytics.analytics.fundamental_factors import fundamental_inputs
    from geoanalytics.analytics.market_sentiment import is_divergent, latest
    from geoanalytics.analytics.prices import asset_indicators

    if indicators is None:
        indicators = asset_indicators(session, asset_id, period=period).as_dict()
    if forecasts is None:
        forecasts = forecasts_for_asset(session, asset_id,
                                        last_price=indicators.get("last"))

    sent = latest(session, "asset", asset_id=asset_id)
    sent_ewma = sent.sent_ewma if sent else None
    breadth = sent.breadth if sent else None

    if with_fundamentals and fundamentals is None:
        fundamentals = fundamental_inputs(session, asset_id)
    quality = fundamentals.get("quality") if fundamentals else None
    fair_val = fundamentals.get("fair_value") if fundamentals else None

    drivers = [
        sentiment_driver(sent_ewma, breadth),
        technical_driver(indicators),
        forecast_driver(forecasts),
        fundamental_driver(quality, fair_val) if with_fundamentals else None,
    ]
    # Профиль весов по горизонту: переопределяем вес каждого драйвера (compose_stance берёт
    # вес из самого Driver, не из WEIGHTS) — long смещает к фундаменталу.
    profile = _HORIZON_WEIGHTS.get(horizon, WEIGHTS)
    for d in drivers:
        if d is not None and d.key in profile:
            d.weight = profile[d.key]

    # Бэктест для модулятора уверенности (C3) — кэшированный, не на каждый ответ заново.
    # Для пакета по портфелю (`with_backtest=False`) пропускаем — десяток прогонов на запрос дорог.
    if backtest is None and with_backtest:
        from geoanalytics.analytics.backtest import backtest_asset_cached
        backtest = backtest_asset_cached(ticker)

    # Риск: разброс мнений, дивергенция, просадка/волатильность, вклад в VaR.
    ret_1m = indicators.get("ret_1m")
    risk: dict = {}
    if sent is not None:
        risk["dispersion"] = round(sent.dispersion, 2)
        risk["diverging"] = is_divergent(ret_1m, sent.sent_ewma)
    if getattr(backtest, "max_drawdown_pct", None):
        risk["max_drawdown_pct"] = backtest.max_drawdown_pct
    if indicators.get("vol_annual") is not None:
        # `volatility()` уже возвращает годовую волатильность В ПРОЦЕНТАХ (×100 внутри) — повторное
        # ×100 раздувало значение в 100× (на карточке актива светилось «3000%/год»). Берём как есть.
        risk["vol_annual_pct"] = round(indicators["vol_annual"], 1)
    if indicators.get("pct_from_52w_high") is not None:
        risk["pct_from_52w_high"] = indicators["pct_from_52w_high"]
    if var_contribution_rub is not None:
        risk["var_contribution_rub"] = round(var_contribution_rub, 0)

    stance = compose_stance(ticker, drivers, n_possible=len(drivers),
                            backtest_factor=_backtest_factor(backtest), risk=risk)
    return apply_quality_gate(stance, quality)
