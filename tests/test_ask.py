"""Тесты RAG-оркестратора (B): чистый разбор интента, фолбэк-эвристика, диспатч с моками."""

from __future__ import annotations

import contextlib

from geoanalytics.query import ask


@contextlib.contextmanager
def _fake_scope():
    yield object()


# --- _parse_intent (чистый разбор JSON от LLM) ---
def test_parse_intent_clean_json():
    obj = ask._parse_intent('{"intent":"asset","ticker":"sber","hours":48,'
                            '"strategy":null,"query":null}')
    assert obj == {"intent": "asset", "ticker": "SBER", "hours": 48,
                   "strategy": None, "query": None}


def test_parse_intent_with_surrounding_text():
    # LLM часто добавляет болтовню вокруг JSON — берём первый сбалансированный объект.
    obj = ask._parse_intent('Вот ответ: {"intent": "market"} — надеюсь, помог!')
    assert obj["intent"] == "market"


def test_parse_intent_rejects_unknown_intent():
    assert ask._parse_intent('{"intent":"weather"}') is None


def test_parse_intent_rejects_garbage():
    assert ask._parse_intent("вообще не JSON") is None
    assert ask._parse_intent("{битый json") is None
    assert ask._parse_intent(None) is None


def test_parse_intent_drops_invalid_strategy_and_hours():
    obj = ask._parse_intent('{"intent":"backtest","ticker":"GAZP",'
                            '"strategy":"voodoo","hours":-5}')
    assert obj["strategy"] is None and obj["hours"] is None


# --- _fallback_intent (чистая эвристика) ---
def test_fallback_ticker_to_asset():
    assert ask._fallback_intent("как дела у сбера", "SBER")["intent"] == "asset"


def test_fallback_backtest_keyword_needs_ticker():
    assert ask._fallback_intent("протестируй стратегию на SBER", "SBER")["intent"] == "backtest"
    # без тикера «стратегия» не уводит в backtest
    assert ask._fallback_intent("какая стратегия лучше", None)["intent"] != "backtest"


def test_fallback_alerts_and_market_and_events():
    assert ask._fallback_intent("покажи алерты", None)["intent"] == "alerts"
    assert ask._fallback_intent("что по рынку сегодня", None)["intent"] == "market"
    assert ask._fallback_intent("какие санкции ввели", None)["intent"] == "events"


def test_fallback_open_question_to_news():
    obj = ask._fallback_intent("что происходит с экспортом нефти", None)
    assert obj["intent"] == "news" and obj["query"]


# --- resolve_intent: LLM-путь и фолбэк ---
def test_resolve_intent_uses_llm_when_valid(monkeypatch):
    monkeypatch.setattr(ask.llm, "generate", lambda *a, **kw: '{"intent":"market"}')
    obj, used = ask.resolve_intent("что по новостям")
    assert used is True and obj["intent"] == "market"


def test_resolve_intent_falls_back_when_llm_none(monkeypatch):
    monkeypatch.setattr(ask.llm, "generate", lambda *a, **kw: None)
    monkeypatch.setattr(ask, "_resolve_ticker", lambda q: None)
    obj, used = ask.resolve_intent("покажи алерты")
    assert used is False and obj["intent"] == "alerts"


def test_resolve_intent_verifies_ticker_for_asset(monkeypatch):
    # LLM выдал выдуманный тикер из имени («роснефти» → ROSNEFT) — валидируем по БД.
    monkeypatch.setattr(ask.llm, "generate",
                        lambda *a, **kw: '{"intent":"asset","ticker":"ROSNEFT"}')
    monkeypatch.setattr(ask, "_verify_or_resolve_ticker", lambda t, q: "ROSN")
    obj, used = ask.resolve_intent("текущая цена роснефти")
    assert used is True and obj["intent"] == "asset" and obj["ticker"] == "ROSN"


def test_resolve_intent_skips_ticker_verify_for_market(monkeypatch):
    # market без тикера не должен зря дёргать EntityIndex/БД.
    monkeypatch.setattr(ask.llm, "generate", lambda *a, **kw: '{"intent":"market"}')
    called = []
    monkeypatch.setattr(ask, "_verify_or_resolve_ticker",
                        lambda t, q: called.append(1) or "X")
    obj, _ = ask.resolve_intent("что по рынку сегодня")
    assert obj["intent"] == "market" and obj["ticker"] is None and not called


# --- _dispatch / answer с моками (без БД/сети) ---
def test_dispatch_news_uses_semantic(monkeypatch):
    monkeypatch.setattr(ask.semantic, "search_news", lambda *a, **kw: [
        {"title": "Санкции против банка", "url": "http://x/1", "sentiment": "negative",
         "significance": 0.8, "published_at": None, "score": 0.82},
    ])
    facts, cites, grounding = ask._dispatch(
        {"intent": "news", "ticker": None, "hours": None, "query": "санкции"}, "санкции")
    assert "Санкции против банка" in facts[0]
    assert cites == [{"title": "Санкции против банка", "url": "http://x/1", "score": 0.82}]
    assert "санкции" in grounding.lower()


def test_dispatch_news_empty_when_semantic_unavailable(monkeypatch):
    monkeypatch.setattr(ask.semantic, "search_news", lambda *a, **kw: [])
    facts, cites, grounding = ask._dispatch(
        {"intent": "news", "ticker": None, "hours": None, "query": "x"}, "x")
    assert cites == [] and grounding == "" and "недоступен" in facts[0].lower()


def test_answer_without_llm_builds_template(monkeypatch):
    # LLM выключен → интент по эвристике, нарратив — шаблон из фактов.
    monkeypatch.setattr(ask.semantic, "search_news", lambda *a, **kw: [
        {"title": "Новость дня", "url": "http://x/2", "sentiment": "neutral",
         "significance": 0.5, "published_at": None, "score": 0.7}])
    monkeypatch.setattr(ask, "_resolve_ticker", lambda q: None)
    res = ask.answer("что нового про экспорт", use_llm=False)
    assert res.intent == "news"
    assert res.used_llm is False and res.note
    assert res.citations and "Новость дня" in res.answer


def test_answer_empty_question():
    res = ask.answer("   ")
    assert res.intent == "unknown" and "вопрос" in res.answer.lower()


def test_answer_degrades_when_llm_busy(monkeypatch):
    """Замок генерации занят (LLMBusy) → ответ собирается БЕЗ ИИ + честная пометка «занята»."""
    import contextlib

    @contextlib.contextmanager
    def _busy():
        raise ask.LLMBusy()
        yield  # noqa: для генератора-контекстменеджера

    seen = {}

    def _impl(q, *, use_llm, user_id):
        seen["use_llm"] = use_llm
        return ask.AskResult(question=q, intent="news", answer="шаблон", used_llm=use_llm)

    monkeypatch.setattr(ask, "llm_generation_lock", _busy)
    monkeypatch.setattr(ask, "_answer_impl", _impl)
    res = ask.answer("что по рынку", use_llm=True)
    assert seen["use_llm"] is False          # деградация на не-LLM путь
    assert "занята" in res.note


def test_answer_uses_llm_when_lock_free(monkeypatch):
    """Замок свободен → генерация идёт с ИИ (use_llm=True доходит до сборки)."""
    import contextlib

    @contextlib.contextmanager
    def _free():
        yield

    seen = {}

    def _impl(q, *, use_llm, user_id):
        seen["use_llm"] = use_llm
        return ask.AskResult(question=q, intent="news", answer="x", used_llm=use_llm)

    monkeypatch.setattr(ask, "llm_generation_lock", _free)
    monkeypatch.setattr(ask, "_answer_impl", _impl)
    ask.answer("вопрос", use_llm=True)
    assert seen["use_llm"] is True


# --- Волна 6: portfolio-aware ask ---
def test_portfolio_recommendation_rules():
    assert "концентрация" in ask._portfolio_recommendation(30.0, 10.0)   # доля ≥25%
    assert "вносит в риск" in ask._portfolio_recommendation(10.0, 20.0)  # риск ≫ доли
    assert "в пределах" in ask._portfolio_recommendation(8.0, 5.0)       # норма


def _report(positions, **kw):
    from geoanalytics.analytics.portfolio import PortfolioReport
    return PortfolioReport(positions=positions, **kw)


def _pos(ticker, **kw):
    from geoanalytics.analytics.portfolio import PositionReport
    return PositionReport(ticker=ticker, quantity=kw.pop("quantity", 10), **kw)


def test_portfolio_block_held_asset(monkeypatch):
    """Тикер в портфеле → структурный блок доля/β/вклад в риск/корреляции + рекомендация."""
    rep = _report(
        [_pos("SBER", weight_pct=30.0, betas={"market": 1.2}, risk_contribution_pct=35.0)],
        var95_1d_rub=1000.0,
        correlations={("SBER", "VTBR"): 0.71, ("SBER", "PLZL"): -0.12})
    monkeypatch.setattr(ask, "owner_portfolio_report", lambda *a, **k: rep)
    pf = ask._portfolio_block("SBER")
    assert pf["weight_pct"] == 30.0 and pf["beta_market"] == 1.2
    assert pf["risk_contribution_pct"] == 35.0
    assert pf["var_contribution_rub"] == 350  # 1000 ₽ VaR × 35% вклада
    assert pf["correlations"][0] == {"ticker": "VTBR", "r": 0.71}  # сильнейшая по |r|
    assert "концентрация" in pf["recommendation"]


def test_portfolio_block_not_held(monkeypatch):
    monkeypatch.setattr(ask, "owner_portfolio_report",
                        lambda *a, **k: _report([_pos("GAZP", weight_pct=100.0)]))
    assert ask._portfolio_block("SBER") is None


def test_portfolio_block_no_ticker_or_no_report(monkeypatch):
    assert ask._portfolio_block(None) is None
    monkeypatch.setattr(ask, "owner_portfolio_report", lambda *a, **k: None)
    assert ask._portfolio_block("SBER") is None


def test_owner_portfolio_report_swallows_errors(monkeypatch):
    """Сбой БД в кэш-раннере → None (ask продолжает без портфеля)."""
    ask._owner_pf_cache.clear()
    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr("geoanalytics.storage.db.session_scope", _boom)
    assert ask.owner_portfolio_report() is None


# --- (б) сценарный парсер + портфельный диспатч ---
def test_parse_shock_direction_and_factor():
    assert ask._parse_shock("что если нефть упадёт на 10%") == {"brent": -10.0}
    assert ask._parse_shock("если рубль ослабнет на 5%") == {"usd_rub": -5.0}
    assert ask._parse_shock("сценарий: золото вырастет на 8%") == {"gold": 8.0}


def test_parse_shock_rejects_non_scenario():
    assert ask._parse_shock("какая доходность 10%") is None      # нет триггера «если/сценарий»
    assert ask._parse_shock("что если санкции") is None          # нет процента/фактора


def test_fallback_intent_portfolio():
    assert ask._fallback_intent("какой у меня риск портфеля", None)["intent"] == "portfolio"
    assert ask._fallback_intent("что если нефть упадёт на 10%", None)["intent"] == "portfolio"


def test_dispatch_portfolio_summary(monkeypatch):
    rep = _report([_pos("SBER", weight_pct=60.0), _pos("PLZL", weight_pct=40.0)],
                  total_value_rub=50000.0, daily_vol_pct=1.4, var95_1d_pct=2.3,
                  var95_1d_rub=1150.0, exposure={"market": 0.9, "brent": -0.2},
                  sector_alloc=[("Банки", 60.0), ("Металлы", 40.0)], regime="спокойный")
    monkeypatch.setattr(ask, "owner_portfolio_report", lambda *a, **k: rep)
    facts, cites, grounding = ask._dispatch_portfolio("какой у меня риск")
    assert any("50,000" in f or "50 000" in f for f in facts)
    assert any("VaR 95%" in f for f in facts)
    assert any("крупнейшие: SBER 60%" in f for f in facts)
    assert "Банки 60%" in grounding


def test_dispatch_portfolio_scenario(monkeypatch):
    from geoanalytics.analytics.whatif import AssetScenario, ScenarioResult
    monkeypatch.setattr(ask, "owner_portfolio_report",
                        lambda *a, **k: _report([_pos("SBER", weight_pct=100.0)],
                                                total_value_rub=1000.0))
    sc = ScenarioResult(shocks_pct={"brent": -10.0}, portfolio_move_pct=-2.5,
                        portfolio_pnl_rub=-25.0, total_value_rub=1000.0,
                        assets=[AssetScenario(ticker="SBER", expected_move_pct=-2.5, r2=0.4)],
                        caveats=["линейно по бетам"])
    monkeypatch.setattr("geoanalytics.analytics.whatif.whatif_portfolio",
                        lambda session, shocks, **k: sc)
    monkeypatch.setattr("geoanalytics.storage.db.session_scope", _fake_scope)
    facts, _c, grounding = ask._dispatch_portfolio("что если нефть упадёт на 10%")
    assert any("портфель -2.50%" in f for f in facts)
    assert any("SBER: -2.50%" in f for f in facts)


# --- (в) RAG-трейс ---
def test_rag_trace_filters_scored_citations():
    cits = [{"title": "A", "url": "u1", "score": 0.83}, {"title": "B", "url": "u2"},
            {"title": "C", "url": "u3", "score": None}]
    trace = ask._rag_trace(cits)
    assert trace == [{"title": "A", "url": "u1", "score": 0.83}]


# --- _clean_narrative: обрезка языкового дрейфа (рус→кит) ---
def test_clean_narrative_drops_chinese_segments_keeps_russian():
    # Посегментно: китайские куски выкидываются, русские (даже после срыва) сохраняются.
    text = ("Сбербанк показывает рост. 当前状态：股价上涨。\n"
            "Ключевые драйверы — ставка ЦБ.")
    out = ask._clean_narrative(text)
    assert out == "Сбербанк показывает рост. Ключевые драйверы — ставка ЦБ."
    assert not any(0x4e00 <= ord(c) <= 0x9fff for c in out)


def test_clean_narrative_dedups_repeats():
    text = "Рынок стабилен. Рынок стабилен. Драйвер — ставка."
    out = ask._clean_narrative(text)
    assert out == "Рынок стабилен. Драйвер — ставка."


def test_clean_narrative_strips_leading_punct():
    assert ask._clean_narrative(". Текущее состояние рынка стабильно.") == \
        "Текущее состояние рынка стабильно."


def test_clean_narrative_keeps_clean_russian():
    text = "Рынок РФ под давлением высокой ставки."
    assert ask._clean_narrative(text) == text


def test_clean_narrative_strips_chat_template_leak():
    # Регресс (живой кейс PLZL): модель дописала фейковый ход диалога — ролевые
    # маркеры срезаются, извинение-мета выкидывается, содержимое после сохраняется.
    text = ("Тренд нисходящий за различные периоды. user Извините за недоразумение. "
            "assistant Цена находится ниже уровней SMA50 и SMA200.")
    out = ask._clean_narrative(text)
    assert out == ("Тренд нисходящий за различные периоды. "
                   "Цена находится ниже уровней SMA50 и SMA200.")


def test_clean_narrative_strips_role_lines():
    text = "system\nОтвет по активу готов.\nuser\nassistant\nДрайвер — ставка ЦБ."
    out = ask._clean_narrative(text)
    assert out == "Ответ по активу готов. Драйвер — ставка ЦБ."


# --- анти-галлюцинация: числа нарратива должны быть в данных ---
def test_digit_runs_extracts_multidigit_only():
    runs = ask._digit_runs("ставка 16%, цена 49,15, всего 3 новости")
    assert "16" in runs
    assert "4915" in runs       # «49,15» → разделитель убран
    assert "3" not in runs      # однозначные пропускаем


def test_strip_unsupported_numbers_drops_fabricated_sentence():
    grounding = "Ключевая ставка ЦБ РФ 16%. Цена SBER 310 рублей."
    text = ("Ставка ЦБ составляет 16%. Прибыль выросла на 47% за квартал. "
            "Рынок под давлением.")
    out = ask._strip_unsupported_numbers(text, grounding)
    assert "16%" in out                  # подтверждено данными
    assert "47%" not in out              # выдумано → удалено
    assert "Рынок под давлением." in out  # без чисел → сохранено


def test_strip_unsupported_numbers_tolerates_rounding():
    grounding = "RSI(14) равен 49,15."
    # Округление 49.15 → 49: руна «49» — подстрока «4915», поддержано.
    assert "49" in ask._strip_unsupported_numbers("Индикатор RSI около 49.", grounding)


def test_strip_unsupported_numbers_keeps_qualitative():
    grounding = "Тональность негативная."
    text = "Настроение ухудшается. Риски сохраняются."
    assert ask._strip_unsupported_numbers(text, grounding) == text
