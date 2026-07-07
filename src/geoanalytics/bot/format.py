"""Форматирование ответов бота под Telegram (plain text, без parse_mode).

Чистые функции: доменный объект (AskResult/AssetReport/PortfolioReport/список алертов) →
строка. Тестируются без сети и БД. Длинные ответы режутся под лимит Telegram (4096).
"""

from __future__ import annotations

_MAX = 3800   # запас под лимит Telegram 4096 (на всякий случай оставляем поле)
_SEV_ICON = {"info": "ℹ️", "warning": "⚠️", "critical": "🔴"}
# Трек B: светофор сигнала стойки/идеи для читаемости (зелёный=покупать … красный=продавать).
_SIGNAL_DOT = {"buy": "🟢", "accumulate": "🟢", "hold": "🟡", "reduce": "🟠", "sell": "🔴"}


def _cap(text: str) -> str:
    """Обрезать ответ под лимит Telegram, не разрывая по середине строки грубо."""
    if len(text) <= _MAX:
        return text
    return text[:_MAX].rsplit("\n", 1)[0] + "\n…"


def help_text(*, is_admin: bool = False) -> str:
    base = (
        "📊 geoanalytics-бот — команды:\n\n"
        "📈 Аналитика\n"
        "/ask <вопрос> — спросить аналитику (напр.: /ask что по Сберу?)\n"
        "/asset <тикер> — карточка актива (напр.: /asset SBER)\n\n"
        "💼 Портфель\n"
        "/portfolio — сводка (стоимость, P&L, риск)\n"
        "/buy <тикер> <кол-во> [цена] — добавить позицию\n"
        "/sell <тикер> — убрать позицию\n"
        "/cash [валюта сумма] — кэш/валюта в составе (напр.: /cash USD 1500)\n\n"
        "🔔 Алерты\n"
        "/alerts — последние сработавшие алерты\n"
        "/mute <тикер|тип> — заглушить лично себе · /mutes — список · /unmute <id>\n\n"
        "/help — эта подсказка"
    )
    if is_admin:
        base += (
            "\n\n👑 Админ\n"
            "/users — список пользователей\n"
            "/grant <telegram_id> — выдать доступ · /revoke <telegram_id> — снять"
        )
    return base


def welcome(user) -> str:
    """Приветствие после /start для авторизованного пользователя."""
    who = f" @{user.username}" if getattr(user, "username", None) else ""
    is_admin = getattr(user, "role", None) == "admin"
    return f"Привет{who}! Доступ открыт.\n\n" + help_text(is_admin=is_admin)


def format_users(rows) -> str:
    """Список пользователей для админа: (telegram_user_id, username, allowed, role)."""
    if not rows:
        return "Пользователей пока нет."
    lines = ["👥 Пользователи:"]
    for tg, username, allowed, role in rows:
        mark = "✅" if allowed else "⏳"
        uname = f"@{username}" if username else "—"
        tag = " · admin" if role == "admin" else ""
        lines.append(f"{mark} {tg} {uname}{tag}")
    lines.append("\nВыдать доступ: /grant <id> · снять: /revoke <id>")
    return _cap("\n".join(lines))


def pending() -> str:
    """Ответ незарегистрированному/неавторизованному после /start."""
    return ("Заявка на доступ принята. Бот ответит, когда администратор подтвердит "
            "ваш аккаунт.")


def format_mutes(rows) -> str:
    """Личные mute-правила пользователя → текст (id для снятия)."""
    if not rows:
        return "У вас нет личных заглушек. Создать — /mute SBER или /mute neg_spike"
    lines = ["🔇 Ваши заглушки:"]
    for m in rows:
        until = f" до {m['until'][:10]}" if m.get("until") else ""
        lines.append(f"#{m['id']} {m['scope_type']}: {m['scope_value']}{until}")
    lines.append("\nСнять: /unmute <id>")
    return _cap("\n".join(lines))


def format_ask(r) -> str:
    """AskResult → текст: ответ + портфельный блок + факты + источники + RAG-трейс."""
    lines = [f"🔍 {r.answer.strip()}"] if r.answer else ["🔍 Нет ответа."]
    pf = getattr(r, "portfolio", None)
    if pf:
        lines.append(f"\n💼 {pf['ticker']} в вашем портфеле:")
        seg = [f"доля {pf['weight_pct']:.1f}%"]
        if pf.get("beta_market") is not None:
            seg.append(f"β к рынку {pf['beta_market']:+.2f}")
        if pf.get("risk_contribution_pct") is not None:
            seg.append(f"вклад в риск {pf['risk_contribution_pct']:.0f}%")
        if pf.get("var_contribution_rub") is not None:
            seg.append(f"в VaR ~{pf['var_contribution_rub']:,.0f} ₽")
        lines.append("• " + ", ".join(seg))
        cors = pf.get("correlations") or []
        if cors:
            lines.append("• корреляции: "
                         + ", ".join(f"{c['ticker']} {c['r']:+.2f}" for c in cors))
        lines.append(f"• рекомендация: {pf['recommendation']}")
    # Трек B: идеи скринера (recommend/help) — светофор сигнала + пояснение + риск + действие.
    recs = getattr(r, "recommendations", None) or []
    if recs:
        lines.append("\n💡 Идеи:")
        for i in recs[:5]:
            dot = _SIGNAL_DOT.get(i.get("signal"), "⚪")
            lines.append(f"{dot} {i['ticker']} ({i['name']}) — {i['label']} → {i['action']}")
            if i.get("rationale"):
                lines.append(f"    за: {i['rationale']}")
            if i.get("risk_note"):
                lines.append(f"    риск: {i['risk_note']}")
    facts = [] if recs else [f for f in (r.facts or []) if f][:5]   # не дублируем идеи фактами
    if facts:
        lines.append("\nФакты:")
        lines += [f"• {f}" for f in facts]
    cites = [c for c in (r.citations or []) if c.get("title")][:3]
    if cites:
        lines.append("\nИсточники:")
        for c in cites:
            url = c.get("url")
            lines.append(f"• {c['title']}" + (f" — {url}" if url else ""))
    trace = [t for t in (getattr(r, "rag_trace", None) or []) if t.get("score") is not None][:3]
    if trace:
        lines.append("\n🔎 RAG-трейс (релевантность):")
        lines += [f"• {t['title']} — {t['score']:.2f}" for t in trace]
    return _cap("\n".join(lines))


def format_asset(r) -> str:
    """AssetReport → краткая карточка: цена/сектор, давление, нарратив, топ-события."""
    if not r.found:
        return f"Тикер {r.ticker} не найден."
    head = r.ticker + (f" — {r.name}" if r.name else "")
    if r.sector:
        head += f" · {r.sector}"
    lines = [head]
    ind = r.indicators or {}
    if ind.get("last") is not None:
        bits = [f"цена {ind['last']}"]
        if ind.get("rsi14") is not None:
            bits.append(f"RSI {ind['rsi14']}")
        lines.append(" · ".join(bits))
    extra = []
    if r.news_pressure_7d is not None:
        extra.append(f"давление 7д {r.news_pressure_7d:.2f}")
    if r.sentiment_ema_14d is not None:
        extra.append(f"сентимент {r.sentiment_ema_14d:+.2f}")
    if extra:
        lines.append(" · ".join(extra))
    st = getattr(r, "stance", None)
    if st:
        tag = {"buy": "🟢", "accumulate": "🟢", "hold": "⚪",
               "reduce": "🔴", "sell": "🔴"}.get(st["signal"], "⚪")
        lines.append(f"\n{tag} Стойка: {st['label']} "
                     f"(уверенность {round(st['conviction'] * 100)}%, балл {st['score']:+})")
        if st.get("drivers"):
            top = st["drivers"][:3]
            dt = " · ".join(f"{'↑' if d['sign'] > 0 else '↓' if d['sign'] < 0 else '•'}"
                            f"{d['label']}" for d in top)
            lines.append(dt)
    funds = getattr(r, "fundamentals", None) or []
    if funds:
        lines.append("\n📊 Фундаменталка: "
                     + " · ".join(f"{f['label']} {f['display']}" for f in funds[:4]))
    if r.narrative:
        lines.append("\n" + r.narrative.strip().split("\n")[0])
    events = (r.events or [])[:3]
    if events:
        lines.append("\nТоп-события:")
        for e in events:
            mag = e.get("magnitude")
            tag = {"positive": "↑", "negative": "↓"}.get(e.get("direction"), "•")
            title = e.get("title") or ""
            lines.append(f"{tag} {title}" + (f" ({mag:.2f})" if mag is not None else ""))
    return _cap("\n".join(lines))


def format_portfolio(rep) -> str:
    """PortfolioReport → сводка: стоимость, позиции (вес/P&L), риск."""
    if rep.error:
        return f"Портфель: {rep.error}"
    lines = [f"💼 Портфель: {rep.total_value_rub:,.0f} ₽".replace(",", " ")
             + (f" · режим {rep.regime}" if rep.regime else "")]
    lines.append("\nПозиции:")
    for p in rep.positions:
        if p.last_close is None:
            lines.append(f"• {p.ticker} {p.quantity:g} — нет цены")
            continue
        val = f"{p.value_rub:,.0f}".replace(",", " ")
        seg = f"• {p.ticker} {p.quantity:g}×{p.last_close:g} = {val} ₽"
        tail = []
        if p.weight_pct is not None:
            tail.append(f"{p.weight_pct:g}%")
        if p.pnl_pct is not None:
            tail.append(f"P&L {p.pnl_pct:+g}%")
        if tail:
            seg += " (" + ", ".join(tail) + ")"
        lines.append(seg)
    risk = []
    if rep.daily_vol_pct is not None:
        risk.append(f"вол {rep.daily_vol_pct:g}%")
    if rep.var95_1d_pct is not None:
        risk.append(f"VaR95 {rep.var95_1d_pct:g}%")
    if rep.max_drawdown_pct is not None:
        risk.append(f"просадка {rep.max_drawdown_pct:g}%")
    if risk:
        lines.append("\nРиск: " + " · ".join(risk))
    return _cap("\n".join(lines))


def format_alerts(rows) -> str:
    """Список алертов (dict из recent_alerts) → текст."""
    if not rows:
        return "Свежих алертов нет."
    lines = [f"🔔 Алерты ({len(rows)}):"]
    for a in rows:
        icon = _SEV_ICON.get(a.get("severity"), "")
        when = (a.get("created_at") or "")[:16].replace("T", " ")
        tkr = f" · {a['ticker']}" if a.get("ticker") else ""
        lines.append(f"{icon} {a.get('title', '')}{tkr} · {when}")
    return _cap("\n".join(lines))
