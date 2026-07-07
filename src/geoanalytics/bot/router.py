"""Разбор и маршрутизация команд бота (Волна 5a).

`parse_command` — чистый разбор «/cmd аргументы». `dispatch` вызывает доменный раннер
(`ask`/`asset_report`/`portfolio`/`alerts_feed`) и форматирует ответ под Telegram. Раннеры
импортируются ЛЕНИВО — модуль роутится без тяжёлых зависимостей и тестируется с подменой.
"""

from __future__ import annotations

from geoanalytics.bot import format as fmt

COMMANDS = ("start", "help", "ask", "asset", "portfolio", "buy", "sell", "cash", "alerts",
            "mute", "unmute", "mutes", "users", "grant", "revoke")

# Типы алертов — чтобы `/mute neg_spike` распознать как mute типа, а не тикера.
_ALERT_TYPES = ("price_move", "neg_spike", "new_event", "technical", "combo",
                "calendar", "portfolio", "health")


def parse_command(text: str) -> tuple[str, str]:
    """«/asset SBER» → ('asset', 'SBER'); «/ask что…» → ('ask', 'что…'); без слэша → ('', text).

    Срезает @botname из команды (Telegram добавляет в группах). Чистая.
    """
    t = (text or "").strip()
    if not t.startswith("/"):
        return "", t
    head, _, rest = t.partition(" ")
    cmd = head[1:].split("@", 1)[0].lower()
    return cmd, rest.strip()


def dispatch(cmd: str, arg: str, *, user=None) -> str:
    """Команда + аргумент → ответ. `user` (5b) — снимок для личных mute. Неизвестная → help."""
    if cmd in ("", "start", "help"):
        return fmt.help_text(is_admin=_is_admin(user))

    if cmd == "ask":
        if not arg:
            return "Напишите вопрос: /ask что по Сберу?"
        from geoanalytics.query.ask import answer
        # Портфельный блок/интент — по портфелю спрашивающего (admin/владелец → общий).
        return fmt.format_ask(answer(arg, user_id=_portfolio_scope(user)))

    if cmd == "asset":
        if not arg:
            return "Укажите тикер: /asset SBER"
        from geoanalytics.query.asset_report import build_report
        return fmt.format_asset(build_report(arg.split()[0].upper()))

    if cmd == "portfolio":
        from geoanalytics.analytics.portfolio import live_portfolio_report
        from geoanalytics.storage.db import session_scope
        with session_scope() as session:
            return fmt.format_portfolio(live_portfolio_report(session,
                                                              user_id=_portfolio_scope(user)))

    if cmd in ("buy", "sell"):
        return _dispatch_trade(cmd, arg, user)

    if cmd == "cash":
        return _dispatch_cash(arg, user)

    if cmd == "alerts":
        from geoanalytics.query.alerts_feed import recent_alerts
        # Изоляция: обычный юзер видит broadcast + свои; админ/владелец — все.
        kw = {} if (user is None or _is_admin(user)) else {"user_id": user.id}
        return fmt.format_alerts(recent_alerts(hours=168, limit=10, **kw))

    if cmd in ("mute", "unmute", "mutes"):
        return _dispatch_mute(cmd, arg, user)

    if cmd in ("users", "grant", "revoke"):
        return _dispatch_admin(cmd, arg, user)

    return f"Неизвестная команда /{cmd}.\n\n" + fmt.help_text(is_admin=_is_admin(user))


def _is_admin(user) -> bool:
    return user is not None and getattr(user, "role", None) == "admin"


def _dispatch_admin(cmd: str, arg: str, user) -> str:
    """Админ-команды онбординга (5b): /users, /grant <id>, /revoke <id>."""
    if not _is_admin(user):
        return "Команда доступна только администраторам."
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import UserRepository

    if cmd == "users":
        with session_scope() as session:
            rows = [(u.telegram_user_id, u.username, u.allowed, u.role)
                    for u in UserRepository(session).list_all()]
        return fmt.format_users(rows)

    tg = arg.strip()
    if not tg.lstrip("-").isdigit():
        return f"Укажите telegram_user_id: /{cmd} 123456789 (список — /users)"
    with session_scope() as session:
        u = UserRepository(session).set_allowed(int(tg), cmd == "grant")
    if u is None:
        return "Нет такого пользователя — он должен сперва написать боту /start."
    return f"Доступ {'выдан' if cmd == 'grant' else 'снят'}: {tg}"


def _portfolio_scope(user) -> int | None:
    """Чей портфель видит/правит бот-пользователь (5c): admin → владельца (общий с дашбордом),
    обычный пользователь → свой личный. Без пользователя — владельца."""
    if user is None or getattr(user, "role", None) == "admin":
        return None
    return user.id


def _dispatch_trade(cmd: str, arg: str, user) -> str:
    """Правка портфеля из бота (5c): /buy ТИКЕР КОЛ-ВО [ЦЕНА], /sell ТИКЕР."""
    if user is None:
        return "Команда доступна только авторизованным пользователям."
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import PortfolioRepository

    scope = _portfolio_scope(user)
    parts = arg.split()
    if cmd == "sell":
        if not parts:
            return "Что продать? /sell SBER"
        with session_scope() as session:
            ok = PortfolioRepository(session, user_id=scope).remove_position(parts[0].upper())
        return f"Позиция {parts[0].upper()} удалена." if ok else "Такой позиции нет."

    # buy: ТИКЕР КОЛ-ВО [ЦЕНА]
    if len(parts) < 2:
        return "Формат: /buy SBER 10 250.5 (цена опц.)"
    ticker = parts[0].upper()
    try:
        qty = float(parts[1].replace(",", "."))
        price = float(parts[2].replace(",", ".")) if len(parts) > 2 else None
    except ValueError:
        return "Кол-во и цена — числа: /buy SBER 10 250.5"
    try:
        with session_scope() as session:
            pos = PortfolioRepository(session, user_id=scope).upsert_position(ticker, qty, price)
    except ValueError:
        return "Количество должно быть положительным."
    if pos is None:
        return f"Тикер {ticker} не найден."
    return f"Добавлено: {ticker} +{qty:g}. Посмотреть — /portfolio"


def _dispatch_cash(arg: str, user) -> str:
    """Кэш/валюта в портфеле (расширение состава): /cash — список, /cash USD 1500 — задать,
    /cash USD 0 — удалить. Скоуп как у портфеля (admin → владелец, иначе свой)."""
    if user is None:
        return "Команда доступна только авторизованным пользователям."
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import CashBalanceRepository

    scope = _portfolio_scope(user)
    parts = arg.split()
    with session_scope() as session:
        repo = CashBalanceRepository(session, user_id=scope)
        if not parts:
            bal = repo.list_balances()
            if not bal:
                return "Кэш не задан. Пример: /cash USD 1500 (или RUB 50000)"
            lines = "\n".join(f"• {ccy}: {amt:,.2f}" for ccy, amt in bal)
            return f"💵 Кэш/валюта:\n{lines}\nПоказать в составе — /portfolio"
        ccy = parts[0].upper()
        if len(parts) < 2:
            return "Сумма? /cash USD 1500 (удалить — /cash USD 0)"
        try:
            amount = float(parts[1].replace(",", "."))
        except ValueError:
            return "Сумма — число: /cash USD 1500"
        if amount <= 0:
            ok = repo.remove(ccy)
            return f"{ccy} удалён." if ok else f"{ccy} не было."
        repo.set_balance(ccy, amount)
    return f"Готово: {ccy} {amount:g}. Состав — /portfolio"


def _dispatch_mute(cmd: str, arg: str, user) -> str:
    """Личные mute-команды (5b): /mute <тикер|тип>, /unmute <id>, /mutes."""
    if user is None:
        return "Команда доступна только авторизованным пользователям."
    from geoanalytics.alerts import manage

    if cmd == "mutes":
        return fmt.format_mutes(manage.list_user_mutes(user.id))

    if cmd == "unmute":
        if not arg.strip().isdigit():
            return "Укажите id правила: /unmute 12 (список — /mutes)"
        ok = manage.unmute(int(arg.strip()), user_id=user.id)
        return "Правило снято." if ok else "Не нашёл такого правила среди ваших."

    # /mute <scope>: тип алерта → mute типа, иначе трактуем как тикер.
    scope = arg.strip().upper()
    if not scope:
        return ("Что заглушить? /mute SBER (тикер) или /mute neg_spike (тип). "
                "Снять — /unmute <id>, список — /mutes")
    if scope.lower() in _ALERT_TYPES:
        st, sv = "type", scope.lower()
    else:
        st, sv = "ticker", scope
    mid = manage.mute(st, sv, user_id=user.id, reason="через бота")
    return f"Заглушено ({st}: {sv}). Снять — /unmute {mid}"
