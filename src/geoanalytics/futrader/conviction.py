"""Трек 2 / Объективный вход (A1–A5): агрегатор conviction над независимыми доказательствами.

Раньше вход был ОДНОЗНАЧНЫМ: одно правило (rsi/momentum/…) задавало направление, GBM-мета-фильтр
лишь гейтил/сайзил. Здесь правило становится ТРИГГЕР-ПРЕДЛОЖЕНИЕМ, а вход подтверждается
СОВОКУПНОСТЬЮ независимых доказательств (как у систематических фондов):

  A1 консенсус стратегий по инструменту (сколько правил согласны в эту сторону),
  A2 мульти-таймфрейм (дневной тренд согласен с часовым входом?),
  A3 Track-1 голоса базового актива (тренд базиса + режим + сентимент — см. `underlying`),
  A4 сценарный стресс (как базис ведёт себя в стандартный risk-off?).

Каждое доказательство — своезнаковый `Driver` (вверх=+, вниз=−). Прозрачный агрегатор
`entry_conviction` через ядро Трека 1 `recommendation.compose_stance` сводит их в знаковый балл +
уверенность, и решает: вход ТОЛЬКО при согласии знака с правилом И достаточной уверенности; размер
∝ уверенности. Conviction — ГЕЙТ/САЙЗЕР, НЕ метка обучения (нет утечки в модель/PBO).

ЧЕСТНАЯ ОГОВОРКА: у сырьевых/FX/индексных фьючерсов нет equity-фундаменталки, поэтому для них
доказательства = консенсус + мульти-ТФ + тренд/режим/сентимент базиса (+сценарий для индекса).
Если доказательств нет вовсе (неизвестный базис / тонкие данные) — НЕ блокируем (fail-open в
мета-фильтр), но и не бустим размер: петля созревания не должна вставать из-за пустого контекста.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from geoanalytics.analytics.recommendation import Driver, _clamp, compose_stance
from geoanalytics.core.logging import get_logger
from geoanalytics.futrader.underlying import _trend_contribution, resolve_underlying

log = get_logger("futrader.conviction")

# Веса доказательств (относительные; compose_stance нормирует на сумму присутствующих).
_W_CONSENSUS = 1.0
_W_DAILY = 0.8
_W_SCENARIO = 0.7

# Стандартный risk-off шок (%/день) для сценарного стресса индексного базиса. Доходности факторов.
STANDARD_RISK_OFF = {"market": -5.0, "brent": -5.0, "usd_rub": 3.0, "gold": 1.0}

MIN_CONVICTION = 0.15          # минимальная уверенность совокупности для входа
RISK_SCALE_LO = 0.5           # множитель target_risk при минимальной проходной уверенности
RISK_SCALE_HI = 1.5           # при максимальной


@dataclass
class EntryConviction:
    """Решение объективного входа: знак/уверенность совокупности + прозрачная разбивка."""

    rule_dir: int                       # сторона, предложенная правилом (+1 лонг / −1 шорт)
    score: float = 0.0                  # знаковый балл совокупности доказательств [−1,1]
    conviction: float = 0.0            # уверенность [0,1]
    passes: bool = True                # вход разрешён (согласие знака И уверенность ≥ порога)
    drivers: list = field(default_factory=list)
    reason: str = ""                   # почему заблокировано (для лога): "disagree"/"weak"

    @property
    def risk_multiplier(self) -> float:
        """Множитель target_risk ∝ уверенности (больше согласие → больше риск); 1.0 без голосов."""
        if not self.drivers:
            return 1.0
        return round(RISK_SCALE_LO + (RISK_SCALE_HI - RISK_SCALE_LO) * self.conviction, 3)

    def as_breakdown(self) -> list[dict]:
        """Разбивка для журнала сделки/панели: метка, знак, вклад."""
        return [{"key": d.key, "label": d.label, "sign": d.sign,
                 "contribution": round(d.contribution, 3), "detail": d.detail}
                for d in self.drivers]


def consensus_driver(signals_by_strat: dict[str, list[int]], idx: int) -> Driver | None:
    """A1: нетто-голос ВСЕХ направленных стратегий по инструменту на баре `idx`.

    `signals_by_strat` — {имя: список −1/0/+1}. Доля согласных и сторона → знаковый вклад.
    Изолированные боты больше не действуют поодиночке: вход хочет СОГЛАСИЯ правил."""
    votes = [s[idx] for s in signals_by_strat.values() if idx < len(s)]
    nonzero = [v for v in votes if v]
    if not nonzero:
        return None
    net = sum(nonzero) / len(nonzero)        # ∈ [−1,1]
    n_long = sum(1 for v in nonzero if v > 0)
    n_short = len(nonzero) - n_long
    return Driver("consensus", "Консенсус стратегий", _clamp(net), _W_CONSENSUS,
                  f"{n_long}↑ / {n_short}↓ из {len(votes)} правил")


def daily_trend_driver(session, ticker: str) -> Driver | None:
    """A2: дневной тренд (старший ТФ) — подтверждает/противоречит часовому входу.

    Старший таймфрейм задаёт контекст, младший — тайминг. Через готовую дневную склейку."""
    from geoanalytics.futrader.continuous import continuous_series

    try:
        daily = continuous_series(session, ticker, interval="1d")
    except Exception as exc:  # noqa: BLE001 — нет дневного ряда → просто нет голоса
        log.warning("daily_trend_failed", ticker=ticker, error=str(exc))
        return None
    closes = [b.close for b in daily.bars]
    contrib = _trend_contribution(closes, lookback=20)
    if contrib is None:
        return None
    arrow = "↑" if contrib > 0 else ("↓" if contrib < 0 else "→")
    return Driver("daily_trend", "Дневной тренд", contrib, _W_DAILY,
                  f"дневной ТФ {arrow} ({len(closes)} баров)")


def scenario_driver(session, asset_code: str) -> Driver | None:
    """A4: переживёт ли базис стандартный risk-off? Только для индексного базиса (есть беты).

    Сырьё/FX-фьючерсы ≈ свой базис 1:1 (отдельный стресс не добавляет независимой информации) →
    None, без выдумки. Для индекса: беты Трека 1 × стандартный risk-off → ожидаемый ход (знаковый
    — медвежий ход = медвежье доказательство, согласуется с шортом, бьёт по лонгу)."""
    resolved = resolve_underlying(asset_code)
    if resolved is None or resolved[0] != "index":
        return None
    try:
        from geoanalytics.analytics.attribution import attribute_asset
        from geoanalytics.analytics.whatif import scenario_move

        attr = attribute_asset(session, resolved[1])
        if getattr(attr, "error", None) or not getattr(attr, "betas", None):
            return None
        move, _contrib, _missing = scenario_move(attr.betas, STANDARD_RISK_OFF)
    except Exception as exc:  # noqa: BLE001
        log.warning("scenario_failed", asset_code=asset_code, error=str(exc))
        return None
    # ±5% ожидаемого хода = полный вклад; знак = направление ожидаемого движения базиса.
    return Driver("scenario", "Стресс risk-off", _clamp(move / 5.0), _W_SCENARIO,
                  f"ожид. {move:+.1f}% базиса при risk-off")


def gather_entry_drivers(session, *, ticker: str, asset_code: str,
                         signals_by_strat: dict[str, list[int]], idx: int,
                         intraday: bool = True) -> list[Driver]:
    """Собрать инструмент-уровневые (rule-независимые) доказательства для conviction.

    Считается ОДИН раз на инструмент за цикл (кэшируется вызывающим): эти голоса не зависят от
    того, какое правило сработало. Каждый источник изолирован — сбой не валит остальные.

    ЧИСТОТА ТАЙМФРЕЙМА (Фаза A): на ИНТРАДЕЙ-входе старшие-ТФ индикаторы НЕ участвуют — `intraday`
    отключает A2 (дневной тренд 1d) и A3 (дневной тренд/режим/сентимент базиса). Остаются A1
    (консенсус на ТОМ ЖЕ ТФ) и A4 (макро-сценарий risk-off — не ТФ-индикатор). Дневная торговля
    (intraday=False) получает полный набор A1–A4."""
    from geoanalytics.futrader.underlying import underlying_drivers

    drivers: list[Driver] = []
    cons = consensus_driver(signals_by_strat, idx)             # A1 (на торговом ТФ)
    if cons is not None:
        drivers.append(cons)
    if not intraday:                                  # старший ТФ — только дневная торговля
        daily = daily_trend_driver(session, ticker)           # A2
        if daily is not None:
            drivers.append(daily)
        drivers.extend(underlying_drivers(session, asset_code))   # A3
    scen = scenario_driver(session, asset_code)               # A4 (макро-сценарий, не ТФ-индикатор)
    if scen is not None:
        drivers.append(scen)
    return drivers


def entry_conviction(rule_dir: int, drivers: list[Driver], *,
                     min_conviction: float = MIN_CONVICTION,
                     disagree_veto: float = 0.0) -> EntryConviction:
    """Чистое ядро: совокупность доказательств → решение объективного входа.

    При СОГЛАСИИ знака со стороной правила вход проходит, если уверенность ≥ `min_conviction`.
    При РАСХОЖДЕНИИ — мягкое настраиваемое вето: блокируем вход против совокупности ТОЛЬКО если
    встречная уверенность ≥ `disagree_veto`; слабое/неоднозначное расхождение НЕ вето, решает
    правило+мета-фильтр (тонкая зрелость, торгуем «с допустимым риском»). disagree_veto=0 → строго:
    любое расхождение блокирует (исходно). Пустой набор доказательств → fail-open (passes=True,
    нейтральный множитель риска): петля созревания не встаёт без Track-1.
    """
    present = [d for d in drivers if d is not None]
    if not present:
        return EntryConviction(rule_dir=rule_dir, passes=True, drivers=[])
    # n_possible = число собранных доказательств → completeness судит СОГЛАСИЕ, не абсолютный счёт.
    stance = compose_stance("FUT", present, n_possible=len(present))
    score_dir = 1 if stance.score > 0 else (-1 if stance.score < 0 else 0)
    agree = (score_dir == rule_dir)
    if agree:
        ok = stance.conviction >= min_conviction
        reason = "" if ok else "weak"
    else:
        ok = stance.conviction < disagree_veto       # вето лишь при достаточно сильном встречном
        reason = "" if ok else "disagree"
    return EntryConviction(rule_dir=rule_dir, score=stance.score, conviction=stance.conviction,
                           passes=ok, drivers=stance.drivers, reason=reason)
