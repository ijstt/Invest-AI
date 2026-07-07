"""Трек B: скринер вселенной — ранжированные идеи «что купить / докупить» для инвестора.

Назначение: ответить новичку на «что мне купить?», «что докупить?», «куда вложить?» —
не сырым поиском новостей, а ранжированным списком идей с простым пояснением и риском.

Переиспользует движок стоек `recommendation.stance_for_asset` (с `with_backtest=False` —
пакетный расчёт по всей вселенной дорог с бэктестом), как `portfolio_stance`. Чистое чтение,
ничего не пишет. Вселенная — торгуемые акции и фонды (фьючерсы/индекс не для «купить»).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from geoanalytics.analytics.prices import asset_indicators
from geoanalytics.analytics.recommendation import stance_for_asset
from geoanalytics.core.logging import get_logger
from geoanalytics.storage.models import Asset
from geoanalytics.storage.repositories import PortfolioRepository

log = get_logger("screen")

SCREEN_KINDS = ("share", "fund")          # вселенная скрина (не future/index)
_BUYISH = ("buy", "accumulate")
_SELLISH = ("reduce", "sell")


@dataclass
class ScreenIdea:
    """Одна идея скринера: актив + сигнал + простое пояснение + риск + действие."""

    ticker: str
    name: str
    signal: str               # buy | accumulate | hold | reduce | sell
    label: str                # человекочитаемый сигнал («Покупать» …)
    score: float              # композитный балл [−1, +1]
    conviction: float         # уверенность [0, 1]
    rationale: str            # простое пояснение (топ-драйверы)
    risk_note: str            # на что обратить внимание по риску
    held: bool                # уже в портфеле пользователя
    action: str               # докупить | присмотреться | держать | сократить

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker, "name": self.name, "signal": self.signal,
            "label": self.label, "score": round(self.score, 3),
            "conviction": round(self.conviction, 3), "rationale": self.rationale,
            "risk_note": self.risk_note, "held": self.held, "action": self.action,
        }


def _action(signal: str, held: bool) -> str:
    """Подсказка действия для инвестора с учётом, держит ли он актив."""
    if held:
        if signal in _BUYISH:
            return "докупить"
        if signal in _SELLISH:
            return "сократить"
        return "держать"
    if signal in _BUYISH:
        return "присмотреться"
    return "пропустить"


def _rationale(drivers) -> str:
    """Простое пояснение из топ-2 знаковых драйверов стойки (со стрелкой направления)."""
    parts: list[str] = []
    for d in drivers[:2]:
        if d.contribution == 0:
            continue
        arrow = "↑" if d.contribution > 0 else "↓"
        parts.append(f"{d.label} {arrow}")
    return ", ".join(parts) or "сигналы нейтральны"


def _risk_note(risk: dict) -> str:
    """Короткая заметка о риске из risk-словаря стойки (волатильность/расхождение/просадка)."""
    notes: list[str] = []
    vol = risk.get("vol_annual_pct")
    if vol and 0 < vol <= 200:                # sanity-гейт: аномалии новичку не показываем
        notes.append(f"волатильность ~{vol:.0f}%/год")
    if risk.get("diverging"):
        notes.append("цена расходится с настроением рынка")
    if risk.get("pct_from_52w_high") is not None:
        notes.append(f"{abs(risk['pct_from_52w_high']):.0f}% от годового максимума")
    return "; ".join(notes) or "явных рисков по данным нет"


def screen_universe(session, *, user_id: int | None = None, mode: str = "auto",
                    horizon: str = "swing", period: str = "W",
                    limit: int = 5) -> list[ScreenIdea]:
    """Ранжированные инвест-идеи по вселенной (акции+фонды). НЕ инвестрекомендация (образовательно).

    `mode`: "new" — только НЕ в портфеле (новые идеи); "topup" — только из портфеля (что докупить);
    "auto" — лучшие идеи в целом (held помечаются «докупить»). Ранжирование — «покупательность»:
    buy/accumulate выше, затем балл×уверенность. `with_backtest=False` (пакетный расчёт дорог).
    """
    held = {a.ticker for a, _ in
            PortfolioRepository(session, user_id=user_id).list_positions()}
    assets = session.scalars(select(Asset).where(Asset.kind.in_(SCREEN_KINDS))).all()
    ideas: list[ScreenIdea] = []
    for asset in assets:
        try:
            ind = asset_indicators(session, asset.id, period=period).as_dict()
        except Exception as exc:  # noqa: BLE001 — один актив без истории не валит скрин
            log.warning("screen_indicators_failed", ticker=asset.ticker, error=str(exc))
            continue
        if not ind:
            continue
        try:
            st = stance_for_asset(session, asset.id, asset.ticker, indicators=ind,
                                  with_backtest=False, with_fundamentals=False,
                                  period=period, horizon=horizon)
        except Exception as exc:  # noqa: BLE001
            log.warning("screen_stance_failed", ticker=asset.ticker, error=str(exc))
            continue
        is_held = asset.ticker in held
        ideas.append(ScreenIdea(
            ticker=asset.ticker, name=asset.name, signal=st.signal, label=st.label,
            score=round(st.score, 3), conviction=round(st.conviction, 3),
            rationale=_rationale(st.drivers), risk_note=_risk_note(st.risk),
            held=is_held, action=_action(st.signal, is_held),
        ))

    if mode == "new":
        ideas = [i for i in ideas if not i.held]
    elif mode == "topup":
        ideas = [i for i in ideas if i.held]
    # «Покупательность»: buy/accumulate первыми, затем по баллу×уверенности (сильнее → выше).
    ideas.sort(key=lambda i: (i.signal in _BUYISH, i.score * i.conviction), reverse=True)
    log.info("screen_universe", mode=mode, scanned=len(assets), ideas=len(ideas), limit=limit)
    return ideas[:limit]
