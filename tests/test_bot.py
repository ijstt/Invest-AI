"""Тесты входящего бота (Волна 5a): разбор команд, маршрутизация, формат, авторизация."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from geoanalytics.bot import format as fmt
from geoanalytics.bot.router import dispatch, parse_command
from geoanalytics.bot.service import _handle_update


# --------------------------------------------------------------------------- #
# parse_command
# --------------------------------------------------------------------------- #
def test_parse_command_variants():
    assert parse_command("/asset SBER") == ("asset", "SBER")
    assert parse_command("/ask что по Сберу?") == ("ask", "что по Сберу?")
    assert parse_command("/portfolio") == ("portfolio", "")
    assert parse_command("/asset@geo_bot LKOH") == ("asset", "LKOH")  # @botname срезан
    assert parse_command("просто текст") == ("", "просто текст")      # без слэша
    assert parse_command("  ") == ("", "")


# --------------------------------------------------------------------------- #
# dispatch (раннеры подменяются)
# --------------------------------------------------------------------------- #
def test_dispatch_help_and_unknown():
    assert "/ask" in dispatch("help", "")
    assert "/ask" in dispatch("start", "")
    assert "Неизвестная команда" in dispatch("frobnicate", "")


def test_dispatch_ask_calls_runner(monkeypatch):
    seen = {}

    def fake_answer(q, **k):
        seen["q"] = q
        return SimpleNamespace(answer="Сбер растёт.", facts=["P/E 4.2"],
                               citations=[{"title": "РБК", "url": "http://x"}])

    monkeypatch.setattr("geoanalytics.query.ask.answer", fake_answer)
    out = dispatch("ask", "как Сбер?")
    assert seen["q"] == "как Сбер?"
    assert "Сбер растёт." in out and "P/E 4.2" in out and "РБК" in out


def test_dispatch_ask_empty_arg_prompts():
    assert "Напишите вопрос" in dispatch("ask", "")


def test_dispatch_asset_uppercases_ticker(monkeypatch):
    seen = {}

    def fake_report(t, **k):
        seen["t"] = t
        return SimpleNamespace(found=True, ticker=t, name="Сбербанк", sector="Банки",
                               indicators={"last": 323.1, "rsi14": 55.0},
                               news_pressure_7d=2.1, sentiment_ema_14d=0.36,
                               narrative="Сильный отчёт.", events=[])

    monkeypatch.setattr("geoanalytics.query.asset_report.build_report", fake_report)
    out = dispatch("asset", "sber")
    assert seen["t"] == "SBER"
    assert "Сбербанк" in out and "Банки" in out


def test_dispatch_portfolio(monkeypatch):
    rep = SimpleNamespace(error=None, total_value_rub=30620.0, regime="спокойный",
                          positions=[SimpleNamespace(ticker="SBER", quantity=23,
                                     last_close=324.0, value_rub=7452.0,
                                     weight_pct=24.3, pnl_pct=4.8)],
                          daily_vol_pct=1.02, var95_1d_pct=1.6, max_drawdown_pct=14.59)
    monkeypatch.setattr("geoanalytics.analytics.portfolio.live_portfolio_report",
                        lambda s, **k: rep)

    @contextmanager
    def fake_scope():
        yield object()

    monkeypatch.setattr("geoanalytics.storage.db.session_scope", fake_scope)
    out = dispatch("portfolio", "")
    assert "SBER" in out and "30 620" in out and "VaR95" in out


def test_dispatch_alerts(monkeypatch):
    monkeypatch.setattr("geoanalytics.query.alerts_feed.recent_alerts",
                        lambda **k: [{"severity": "warning", "title": "Скачок негатива",
                                      "ticker": "GAZP", "created_at": "2026-06-15T11:09:00"}])
    out = dispatch("alerts", "")
    assert "Скачок негатива" in out and "GAZP" in out


def test_dispatch_alerts_scopes_regular_user(monkeypatch):
    """Изоляция: обычный юзер фильтрует ленту по своему user_id; админ — нет."""
    seen = {}
    monkeypatch.setattr("geoanalytics.query.alerts_feed.recent_alerts",
                        lambda **k: seen.update(k) or [])
    dispatch("alerts", "", user=SimpleNamespace(id=42, role="user"))
    assert seen.get("user_id") == 42
    seen.clear()
    dispatch("alerts", "", user=_user(allowed=True))   # admin
    assert "user_id" not in seen


# --------------------------------------------------------------------------- #
# админ-онбординг (5b): /users /grant /revoke
# --------------------------------------------------------------------------- #
class _FakeUserRepo:
    last = {}

    def __init__(self, session):
        pass

    def list_all(self):
        return [SimpleNamespace(telegram_user_id=111, username="a", allowed=True, role="admin"),
                SimpleNamespace(telegram_user_id=222, username="b", allowed=False, role="user")]

    def set_allowed(self, tg, allowed):
        _FakeUserRepo.last = {"tg": tg, "allowed": allowed}
        return SimpleNamespace(telegram_user_id=tg)


def _patch_userrepo(monkeypatch):
    _FakeUserRepo.last = {}

    @contextmanager
    def fake_scope():
        yield object()

    monkeypatch.setattr("geoanalytics.storage.db.session_scope", fake_scope)
    monkeypatch.setattr("geoanalytics.storage.repositories.UserRepository", _FakeUserRepo)


def test_admin_users_list(monkeypatch):
    _patch_userrepo(monkeypatch)
    out = dispatch("users", "", user=_user(allowed=True))
    assert "111" in out and "222" in out


def test_admin_grant(monkeypatch):
    _patch_userrepo(monkeypatch)
    out = dispatch("grant", "222", user=_user(allowed=True))
    assert _FakeUserRepo.last == {"tg": 222, "allowed": True}
    assert "выдан" in out


def test_admin_revoke(monkeypatch):
    _patch_userrepo(monkeypatch)
    dispatch("revoke", "222", user=_user(allowed=True))
    assert _FakeUserRepo.last == {"tg": 222, "allowed": False}


def test_admin_denied_to_regular_user():
    assert "администратор" in dispatch("users", "", user=SimpleNamespace(id=5, role="user"))


def test_admin_grant_needs_numeric_id():
    assert "telegram_user_id" in dispatch("grant", "abc", user=_user(allowed=True))


def test_help_shows_admin_section_only_for_admin():
    assert "Админ" in dispatch("help", "", user=_user(allowed=True))
    assert "Админ" not in dispatch("help", "", user=SimpleNamespace(id=5, role="user"))


# --------------------------------------------------------------------------- #
# format
# --------------------------------------------------------------------------- #
def test_format_asset_not_found():
    out = fmt.format_asset(SimpleNamespace(found=False, ticker="ZZZZ"))
    assert "не найден" in out


def test_format_portfolio_error():
    assert "пуст" in fmt.format_portfolio(SimpleNamespace(error="портфель пуст"))


def test_format_alerts_empty():
    assert fmt.format_alerts([]) == "Свежих алертов нет."


def test_cap_truncates_long_text():
    long = "строка\n" * 2000
    assert len(fmt._cap(long)) <= fmt._MAX + 2


# --------------------------------------------------------------------------- #
# service._handle_update — авторизация (5b, через БД) и rate-limit
# --------------------------------------------------------------------------- #
def _upd(uid, chat_id, text, from_id=None, username=None):
    return {"update_id": uid,
            "message": {"text": text, "chat": {"id": chat_id},
                        "from": {"id": from_id or chat_id, "username": username}}}


def _user(chat_id="111", allowed=True):
    return SimpleNamespace(id=1, telegram_user_id=int(chat_id), chat_id=str(chat_id),
                           username="u", role="admin", allowed=allowed)


def test_handle_update_rejects_unauthorized(monkeypatch):
    sent = []
    monkeypatch.setattr("geoanalytics.bot.service.send_telegram",
                        lambda token, chat, text, **k: sent.append((chat, text)))
    monkeypatch.setattr("geoanalytics.bot.service.identity.authorize", lambda c: None)
    called = []
    monkeypatch.setattr("geoanalytics.bot.service.dispatch",
                        lambda *a, **k: called.append(a) or "ответ")
    _handle_update(_upd(1, 999, "/help"), token="t", last_seen={}, rate_limit=0.0)
    assert called == []                       # роутер не звали
    assert sent and "Нет доступа" in sent[0][1]   # но прислали отказ + подсказку /start


def test_handle_update_start_registers(monkeypatch):
    sent = []
    monkeypatch.setattr("geoanalytics.bot.service.send_telegram",
                        lambda token, chat, text, **k: sent.append((chat, text)))
    monkeypatch.setattr("geoanalytics.bot.service.identity.register",
                        lambda uid, cid, un: _user(cid, allowed=True))
    _handle_update(_upd(1, 111, "/start", username="vlad"), token="t",
                   last_seen={}, rate_limit=0.0)
    assert sent and "Доступ открыт" in sent[0][1]


def test_handle_update_authorized_replies_and_rate_limits(monkeypatch):
    sent = []
    monkeypatch.setattr("geoanalytics.bot.service.send_telegram",
                        lambda token, chat, text, **k: sent.append((chat, text)))
    monkeypatch.setattr("geoanalytics.bot.service.identity.authorize",
                        lambda c: _user(c, allowed=True))
    monkeypatch.setattr("geoanalytics.bot.service.dispatch",
                        lambda cmd, arg, **k: f"ok:{cmd}")
    last_seen: dict[str, float] = {}
    _handle_update(_upd(1, 111, "/portfolio"), token="t",
                   last_seen=last_seen, rate_limit=999.0)
    assert sent == [("111", "ok:portfolio")]
    _handle_update(_upd(2, 111, "/alerts"), token="t",
                   last_seen=last_seen, rate_limit=999.0)
    assert len(sent) == 1   # повтор в окне rate-limit срезан


# --------------------------------------------------------------------------- #
# dispatch личных mute-команд (5b)
# --------------------------------------------------------------------------- #
def test_dispatch_mute_ticker(monkeypatch):
    seen = {}

    def fake_mute(st, sv, **k):
        seen.update(st=st, sv=sv, user_id=k.get("user_id"))
        return 7

    monkeypatch.setattr("geoanalytics.alerts.manage.mute", fake_mute)
    out = dispatch("mute", "sber", user=_user())
    assert seen == {"st": "ticker", "sv": "SBER", "user_id": 1}
    assert "/unmute 7" in out


def test_dispatch_mute_type(monkeypatch):
    seen = {}
    monkeypatch.setattr("geoanalytics.alerts.manage.mute",
                        lambda st, sv, **k: seen.update(st=st, sv=sv) or 8)
    dispatch("mute", "neg_spike", user=_user())
    assert seen == {"st": "type", "sv": "neg_spike"}


def test_dispatch_unmute_requires_ownership(monkeypatch):
    seen = {}
    monkeypatch.setattr("geoanalytics.alerts.manage.unmute",
                        lambda mid, **k: seen.update(mid=mid, user_id=k.get("user_id")) or True)
    out = dispatch("unmute", "12", user=_user())
    assert seen == {"mid": 12, "user_id": 1} and "снято" in out.lower()


def test_dispatch_mute_without_user_denied():
    assert "авторизован" in dispatch("mutes", "", user=None)


# --------------------------------------------------------------------------- #
# per-user портфель (5c)
# --------------------------------------------------------------------------- #
def test_portfolio_scope_admin_vs_user():
    from geoanalytics.bot.router import _portfolio_scope
    assert _portfolio_scope(_user(allowed=True)) is None          # admin → портфель владельца
    regular = SimpleNamespace(id=42, role="user")
    assert _portfolio_scope(regular) == 42                        # обычный → свой


def test_dispatch_portfolio_passes_user_scope(monkeypatch):
    seen = {}

    def fake_report(session, **k):
        seen["user_id"] = k.get("user_id")
        return SimpleNamespace(error="портфель пуст")

    monkeypatch.setattr("geoanalytics.analytics.portfolio.live_portfolio_report", fake_report)

    @contextmanager
    def fake_scope():
        yield object()

    monkeypatch.setattr("geoanalytics.storage.db.session_scope", fake_scope)
    dispatch("portfolio", "", user=SimpleNamespace(id=42, role="user"))
    assert seen["user_id"] == 42         # обычный пользователь видит свой портфель


def test_dispatch_ask_passes_user_scope(monkeypatch):
    """Бот /ask пробрасывает user_id спрашивающего → портфельный блок СВОЙ, не владельца."""
    seen = {}

    def fake_answer(question, **k):
        seen["user_id"] = k.get("user_id")
        return SimpleNamespace(answer="ок", facts=[], citations=[], portfolio=None,
                               rag_trace=[], note="")

    monkeypatch.setattr("geoanalytics.query.ask.answer", fake_answer)
    dispatch("ask", "какой у меня риск", user=SimpleNamespace(id=42, role="user"))
    assert seen["user_id"] == 42         # обычный пользователь → свой портфель
    dispatch("ask", "какой риск", user=SimpleNamespace(id=1, role="admin"))
    assert seen["user_id"] is None       # админ/владелец → общий (владельца)


class _FakeRepo:
    last = {}

    def __init__(self, session, *, user_id=None):
        _FakeRepo.last["user_id"] = user_id

    def upsert_position(self, ticker, qty, price=None):
        _FakeRepo.last.update(op="buy", ticker=ticker, qty=qty, price=price)
        return SimpleNamespace(ticker=ticker)

    def remove_position(self, ticker):
        _FakeRepo.last.update(op="sell", ticker=ticker)
        return True


def _patch_repo(monkeypatch):
    _FakeRepo.last = {}

    @contextmanager
    def fake_scope():
        yield object()

    monkeypatch.setattr("geoanalytics.storage.db.session_scope", fake_scope)
    monkeypatch.setattr("geoanalytics.storage.repositories.PortfolioRepository", _FakeRepo)


def test_dispatch_buy(monkeypatch):
    _patch_repo(monkeypatch)
    out = dispatch("buy", "sber 10 250.5", user=SimpleNamespace(id=42, role="user"))
    assert _FakeRepo.last == {"user_id": 42, "op": "buy", "ticker": "SBER",
                              "qty": 10.0, "price": 250.5}
    assert "SBER" in out


def test_dispatch_buy_admin_uses_owner_portfolio(monkeypatch):
    _patch_repo(monkeypatch)
    dispatch("buy", "LKOH 5", user=_user(allowed=True))   # role=admin
    assert _FakeRepo.last["user_id"] is None              # владелец (общий с дашбордом)
    assert _FakeRepo.last["price"] is None


def test_dispatch_sell(monkeypatch):
    _patch_repo(monkeypatch)
    out = dispatch("sell", "sber", user=SimpleNamespace(id=42, role="user"))
    assert _FakeRepo.last == {"user_id": 42, "op": "sell", "ticker": "SBER"}
    assert "удалена" in out


def test_dispatch_buy_bad_format():
    assert "Формат" in dispatch("buy", "SBER", user=SimpleNamespace(id=1, role="user"))


def test_dispatch_trade_requires_user():
    assert "авторизован" in dispatch("buy", "SBER 10", user=None)


# --- Волна 6: format_ask с портфельным блоком и RAG-трейсом ---
def test_format_ask_portfolio_and_trace():
    r = SimpleNamespace(
        answer="Сбер растёт.", facts=["цена: 320"], citations=[{"title": "N", "url": "u"}],
        portfolio={"ticker": "SBER", "weight_pct": 30.0, "beta_market": 1.2,
                   "risk_contribution_pct": 35.0, "var_contribution_rub": 350,
                   "correlations": [{"ticker": "VTBR", "r": 0.71}],
                   "recommendation": "высокая концентрация"},
        rag_trace=[{"title": "Док", "url": "u", "score": 0.83}])
    out = fmt.format_ask(r)
    assert "SBER в вашем портфеле" in out
    assert "доля 30.0%" in out and "β к рынку +1.20" in out
    assert "в VaR ~350 ₽" in out
    assert "корреляции: VTBR +0.71" in out
    assert "рекомендация: высокая концентрация" in out
    assert "RAG-трейс" in out and "Док — 0.83" in out


def test_format_ask_without_portfolio_omits_block():
    r = SimpleNamespace(answer="Ответ", facts=[], citations=[], portfolio=None, rag_trace=[])
    out = fmt.format_ask(r)
    assert "портфеле" not in out and "RAG" not in out


# --- /cash (расширение состава портфеля) ---
class _FakeCash:
    last = {}

    def __init__(self, session, *, user_id=None):
        _FakeCash.last["user_id"] = user_id

    def list_balances(self):
        return [("USD", 1500.0), ("RUB", 50000.0)]

    def set_balance(self, ccy, amount):
        _FakeCash.last.update(op="set", ccy=ccy, amount=amount)

    def remove(self, ccy):
        _FakeCash.last.update(op="remove", ccy=ccy)
        return True


def _patch_cash(monkeypatch):
    _FakeCash.last = {}

    @contextmanager
    def fake_scope():
        yield object()

    monkeypatch.setattr("geoanalytics.storage.db.session_scope", fake_scope)
    monkeypatch.setattr("geoanalytics.storage.repositories.CashBalanceRepository", _FakeCash)


def test_dispatch_cash_list(monkeypatch):
    _patch_cash(monkeypatch)
    out = dispatch("cash", "", user=SimpleNamespace(id=7, role="user"))
    assert "USD: 1,500" in out and "RUB: 50,000" in out
    assert _FakeCash.last["user_id"] == 7


def test_dispatch_cash_set(monkeypatch):
    _patch_cash(monkeypatch)
    out = dispatch("cash", "usd 1500", user=SimpleNamespace(id=7, role="user"))
    assert _FakeCash.last == {"user_id": 7, "op": "set", "ccy": "USD", "amount": 1500.0}
    assert "USD" in out


def test_dispatch_cash_remove(monkeypatch):
    _patch_cash(monkeypatch)
    dispatch("cash", "USD 0", user=SimpleNamespace(id=7, role="user"))
    assert _FakeCash.last["op"] == "remove" and _FakeCash.last["ccy"] == "USD"


def test_dispatch_cash_requires_user():
    assert "авторизован" in dispatch("cash", "USD 100", user=None)
