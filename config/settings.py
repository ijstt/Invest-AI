"""Глобальные настройки приложения (читаются из окружения и .env)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация всего приложения.

    Все переменные окружения имеют префикс ``GEO_`` (см. .env.example).
    """

    model_config = SettingsConfigDict(
        env_prefix="GEO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- База данных ---
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "geo"
    db_password: str = "geo"
    db_name: str = "geoanalytics"

    # --- LLM ---
    # Провайдер тяжёлого синтеза: "local" (Ollama) или "cloud" (внешний API).
    llm_provider: str = "local"
    ollama_host: str = "http://localhost:11434"
    llm_model: str = "qwen2.5:7b-instruct"
    # Окно контекста LLM. Должно вмещать длинный RAG-промпт; дефолт Ollama (2048)
    # обрезал бы его. Поднимаем до 8192 (баланс качества и RAM на слабом железе).
    llm_num_ctx: int = 8192
    # Потолок длины ответа. На CPU генерация ~5 ток/с, поэтому без ограничения
    # модель «растекается» и упирается в таймаут. 640 токенов ≈ богатый структурный
    # анализ (секции: техника/макро/сектор/события/корреляции) за ~2 мин.
    llm_num_predict: int = 640
    # Температура генерации. Для аналитики нужна низкая: дефолт Ollama (~0.7) даёт
    # галлюцинации и языковые срывы (рус+кит у Qwen). 0.3 — фактологично, но не сухо.
    llm_temperature: float = 0.3
    # Таймаут HTTP-запроса к LLM, сек. На CPU холодный вызов = загрузка модели (~15-30с)
    # + prompt_eval длинного RAG-промпта (~20-40с) + генерация (~60с). Берём с запасом.
    llm_timeout: float = 240.0
    # Сколько Ollama держит модель в RAM после запроса. Иначе 5.5 ГБ выгружаются и
    # каждый вызов платит за повторную загрузку. "30m" — компромисс с дефицитом памяти.
    llm_keep_alive: str = "30m"

    # --- Модель ask-пути (B, query/ask.py) ---
    # Единый 7B для интента И нарратива: лёгкая 3B давала языковые срывы (рус+кит) и
    # галлюцинации на аналитике. 7B грузится один раз и держится keep_alive между интентом
    # и нарративом — нет swap моделей. Пик RAM ~9 ГБ в 15 (эмбеддер 1.5 + 7B 5 + overhead),
    # проверено. Для экономии (ценой качества) можно вернуть 3B через GEO_LLM_ROUTER_MODEL.
    llm_router_model: str = "qwen2.5:7b-instruct"
    # Полный контекст для богатого grounding аналитики (секции техника/макро/сектор/события).
    llm_router_num_ctx: int = 8192
    # Держим модель в RAM как основную: вопросы редкие, но повторная загрузка 5 ГБ дорога.
    llm_router_keep_alive: str = "30m"

    cloud_api_key: str | None = None
    cloud_base_url: str | None = None

    # --- NLP-модели каскада ---
    # bge-m3 не поддерживается fastembed (нет в TextEmbedding); multilingual-e5-large
    # — мультиязычная замена той же размерности 1024 (EMBEDDING_DIM), знает русский.
    embedding_model: str = "intfloat/multilingual-e5-large"
    # Каталог кэша весов fastembed (ONNX ~2 ГБ). КРИТИЧНО: дефолт fastembed = /tmp,
    # который эфемерен (чистится при ребуте/tmpwatch) → веса исчезают и семантика/RAG
    # молча отваливаются (см. m0-known-issues). Держим в постоянном data/ (как адаптеры).
    # None → дефолт fastembed (/tmp, НЕ рекомендуется). Путь относительный к корню репо
    # (службы запускаются с WorkingDirectory=репо).
    embedding_cache_dir: str | None = "data/fastembed_cache"
    sentiment_model: str = "blanchefort/rubert-base-cased-sentiment"
    # Путь к дообученному LoRA-адаптеру сентимента (M4). Если задан и доступен peft —
    # поверх базовой ruBERT накладывается адаптер; иначе используется базовая модель.
    sentiment_adapter_path: str | None = None
    # Дообученные LoRA-классификаторы (M6.5). Если путь задан и модель грузится — заменяет
    # правиловую классификацию/формулу; иначе graceful-фолбэк (правила classify / формула sig).
    event_adapter_path: str | None = None
    significance_adapter_path: str | None = None
    # F1/F2 (Волна 2): aspect-тональность и салиентность пары (статья, актив).
    # Не задано/не грузится → graceful-фолбэк: связи получают копию тональности
    # статьи (поведение до F1), салиентность NULL (= салиентно).
    aspect_sentiment_adapter_path: str | None = None
    saliency_adapter_path: str | None = None
    # F3 (Волна 3): временной статус новости (past/future/forecast/none).
    # Не задано/не грузится → статус и дата события NULL (якорь = день публикации).
    temporal_adapter_path: str | None = None

    # --- Значимость новости (M6, nlp.significance) ---
    # Веса слагаемых формулы significance = w_type·тип + w_sent·|тональность| + w_link·связи.
    # По умолчанию суммируются в 1.0 → результат в [0,1].
    sig_w_type: float = 0.5
    sig_w_sent: float = 0.3
    sig_w_link: float = 0.2
    # Фильтр инжеста: новость со significance < порога И без связанных активов И типа OTHER
    # не сохраняется (мусорный шум). 0 — сохранять всё (поведение как до M6).
    min_significance: float = 0.2
    # TTL-ретеншн: срок хранения новости (дней) линейно растёт со значимостью от min до max.
    # significance=0 → min_days, significance=1 → max_days. Старше — удаляются `geo prune`.
    retention_min_days: int = 7
    retention_max_days: int = 365
    # TTL для «сиротских» сырых документов (raw без статьи — отсеянный на инжесте шум).
    # Они не нужны рабочему слою, поэтому чистятся быстро, не дожидаясь retention_max_days.
    raw_retention_days: int = 14
    # Окно дедупа near-duplicate новостей (часов): при обработке не создаём статью, если
    # за это окно уже есть статья с тем же нормализованным хешем заголовка (одна новость
    # от разных лент/источников раздувала счётчики neg-spike алертов). 0 — отключить.
    dedup_window_hours: int = 72

    # --- Внешние источники (M4) ---
    # Ключ API FRED (ФРС, St. Louis Fed). Без него коннектор fred пропускается.
    # Бесплатно: https://fred.stlouisfed.org/docs/api/api_key.html
    fred_api_key: str | None = None

    # --- Алерты (M5.3) ---
    # Окно, в котором движок ищет триггеры (движения, всплеск негатива, события), часов.
    alert_window_hours: int = 24
    # Триггер «движение цены»: |изменение close день-к-дню| ≥ порога, %.
    # Используется как ФОЛБЭК, когда z-режим выключен или у актива нет истории для σ.
    alert_price_pct: float = 5.0
    # G1 (Волна 2): vol-нормализация — триггер по z-score = |движение|/σ(EWMA дневных
    # доходностей). 5% у голубой фишки и третьего эшелона — события разного масштаба.
    # 0 — выключить z-режим (вернуться к фикс. порогу). min_pct — floor: даже при
    # высоком z микродвижение (<floor, %) сверхспокойного актива не алертится.
    alert_price_zscore: float = 3.0
    alert_price_min_pct: float = 1.5
    # Триггер «всплеск негатива»: минимум негативных новостей за окно И их доля ≥ ratio.
    alert_neg_count: int = 3
    alert_neg_ratio: float = 0.5
    # Gate значимости (M6): алерты учитывают только новости со significance ≥ порога —
    # отсекаем шум, оставляем важное. 0 — отключить gate (поведение как до M6).
    alert_min_significance: float = 0.35
    # Типы событий, для которых new_event-алерт идёт ТОЛЬКО при наличии затронутого актива
    # (asset-impact). Лечит шум: ~89% событий (в основном geopolitics) приходят без привязки
    # к активу и неоценимы. Список через запятую; пусто — гейт выключен (как до D1).
    alert_require_impact_types: str = "geopolitics"
    # Виды активов (Asset.kind), исключённые из ВСЕХ алертов: фонды денежного рынка (kind=fund,
    # LQDT/SBMM/…) монотонно растут — price/technical/event-алерты по ним бессмысленны. Запятая.
    alert_exclude_kinds: str = "fund"
    # Источники (Article.source_ref — telegram-канал), чьи посты НЕ порождают новостные алерты
    # (neg_spike/new_event): сигнально-аналитические/торговые каналы (Trendoman и т.п.) — это
    # мнения/сигналы, не новости. На общий сентимент они продолжают влиять. Список через запятую.
    alert_exclude_sources: str = ""
    # Технические алерты (D2): RSI-экстремумы, новый 52w-хай/лой, golden/death cross, всплеск
    # объёма. Дедуп по дню. False — выключить весь технический триггер.
    alert_technical_enabled: bool = True
    alert_rsi_low: float = 30.0          # RSI ≤ → перепроданность
    alert_rsi_high: float = 70.0         # RSI ≥ → перекупленность
    alert_vol_spike_ratio: float = 3.0   # объём/ср.20 ≥ → всплеск
    # Комбо-сигналы (D3): падение цены И всплеск негатива по одному активу в один
    # день → один усиленный critical-алерт (совпадение независимых триггеров весомее).
    # False — выключить. Не подавляет исходные price_move/neg_spike, а дополняет их.
    alert_combo_enabled: bool = True
    # Сюжеты (F6, Волна 2): кластеризация статей по эмбеддингам. distance — порог
    # cosine-дистанции присоединения к сюжету (калибровка 2026-06-11: один сюжет ≤0.12,
    # случайные пары ≥0.22; недослияние безопаснее переслияния). window — окно поиска
    # соседей по времени публикации, часов.
    story_distance: float = 0.12
    story_window_hours: int = 72
    # Исходы алертов (E4, Волна 1): через сколько ТОРГОВЫХ дней скорить алерт и какой
    # порог |движения − IMOEX| (%) считать подтверждением (hit). Precision по типам
    # алертов из этих исходов — глобальная метрика системы (еженедельный отчёт).
    alert_outcome_horizon_days: int = 3
    alert_outcome_move_pct: float = 2.0
    # Календарь (H2, Волна 3): проактивные алерты «скоро событие» (заседание ЦБ,
    # дивидендная отсечка). days_ahead — за сколько дней предупреждать (1 = сегодня
    # и завтра); дедуп — одно уведомление на событие. False — выключить триггер.
    alert_calendar_enabled: bool = True
    alert_calendar_days_ahead: int = 1
    # Алерты по портфелю (#6): просадка портфеля сверх порога и позиции в глубоком минусе.
    # Дедуп по дню. False — выключить. Пустой портфель — тихо пропускается.
    alert_portfolio_enabled: bool = True
    alert_portfolio_drawdown_pct: float = 10.0   # |max просадка портфеля| ≥ → алерт
    alert_portfolio_holding_pnl_pct: float = 15.0  # |P&L позиции от avg_price| в минусе ≥ →
    # Доставка в Telegram (опционально). Оба поля заданы → канал активен, иначе пропуск.
    # Токен у @BotFather. GEO_TELEGRAM_CHAT_ID — id получателя ИЛИ несколько через запятую
    # (рассылка нескольким: "111,222"). id узнать через @userinfobot или getUpdates;
    # каждый получатель должен сначала написать боту /start.
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    # SOCKS/HTTP-прокси ТОЛЬКО для исходящих в Telegram (api.telegram.org). Нужен там, где
    # Telegram заблокирован и нет общесистемного VPN (Raspberry Pi: split-tunnel через локальный
    # Xray-SOCKS, см. deploy/pi/xray-config.json). Пусто — без прокси (ноут ходит через свой VPN).
    # Пример: socks5h://127.0.0.1:10808
    telegram_proxy: str | None = None

    # --- Входящий бот (Волна 5a) ---
    # Интерактивный бот: команды /ask /asset /portfolio /alerts через long-poll getUpdates.
    # Отдельная служба `geo run-bot`. Авторизация — пользователи из таблицы users (5b);
    # стартовый allowlist — telegram_chat_id. Выключен по умолчанию (GEO_TELEGRAM_BOT_ENABLED).
    telegram_bot_enabled: bool = False
    bot_poll_timeout_sec: int = 25       # long-poll getUpdates, сек (Telegram держит соединение)
    bot_rate_limit_sec: float = 2.0      # минимальный зазор между командами одного чата

    # --- Планировщик (M6) ---
    # Базовый тик цикла `geo run-scheduler`, сек. 300 = 5 минут. Ниже ~60 не стоит —
    # вырастет нагрузка на источники (MOEX/RSS) без выигрыша (новости выходят реже).
    # Должен быть ≤ наименьшего per-source интервала ниже (иначе тот не выдержится).
    scheduler_interval_sec: int = 300
    # Раздельная частота опроса по типу источника (Фаза D): незачем дёргать дневной CBR/ECB
    # каждые 5 минут. market — интрадей (каждый тик), новости ~15м, макро раз в день.
    market_interval_sec: int = 300
    news_interval_sec: int = 900
    macro_interval_sec: int = 86400
    # --- Трек 2 / Фаза B: интрадей-цикл бумажного трейдера ---
    # Бумажный цикл на торговом ТФ гоняется ВНУТРИ сессии FORTS на этом интервале (сек), отдельно
    # от дневной петли самообучения (та копит/обучает/переоценивает раз в день). 0 — отключить
    # интрадей-цикл (останется только дневной тик). Лёгкий: грузит чемпионов + скорит последний бар.
    futrader_intraday_interval_sec: int = 600   # 10 минут = шаг 10m-бара
    futrader_intraday_interval: str = "10m"     # торговый таймфрейм интрадей-цикла
    # Гейт объективного входа (conviction). Тюнятся под «торговать с допустимым риском, не идеал»:
    # min_conviction — порог уверенности при СОГЛАСИИ совокупности доказательств со стороной;
    # disagree_veto — насколько сильным должно быть ВСТРЕЧНОЕ доказательство, чтобы заблокировать
    # вход против него (0 = строго: любое расхождение блокирует; выше = пропускаем слабые,
    # квалифицированный чемпион торгует чаще, даже против слабого консенсуса).
    futrader_min_conviction: float = 0.15
    futrader_disagree_veto: float = 0.0
    # Абсолютный пол порога мета-фильтра P(win): эффективный порог = min(порог_чемпиона, pwin_floor)
    # при floor>0; иначе порог чемпиона как есть. Мета-фильтр даёт qty=0, если P(win) < порога
    # чемпиона (у каждого свой, ≈0.55–0.65) — главный стопор на тонком эдже. pwin_floor опускает
    # планку до абсолюта (0.40 = «торгуем всё с P(win)≥0.40»), чтобы quality-проверенные чемпионы
    # реально торговали (мета-фильтр на тонкой выборке переуверен). 0 = выкл (порог чемпиона).
    futrader_pwin_floor: float = 0.0

    # --- Telegram-каналы (H3) ---
    # Публичные каналы через веб-превью t.me/s/<имя> (последние ~20 постов, без API-ключей).
    # Через запятую; пусто — источник telegram отключён.
    # F10: брокерские каналы (SberInvestments/tb_invest_official/bcs_world_of_investments)
    # дают и новости, и прогнозы — роутер nlp/forecast.py разводит пост на новостной каскад
    # vs forecast-путь (извлечение целей/дивидендов в таблицу forecasts).
    telegram_channels: str = (
        "ifax_go,centralbank_russia,CAPITALIST_2033,ecotopor,prostoecon,"
        "SberInvestments,tb_invest_official,bcs_world_of_investments"
    )

    # MTProto (Telethon) для ЗАКРЫТЫХ каналов: api_id/api_hash с my.telegram.org +
    # разовый логин (scripts/telegram_login.py) создаёт файл-сессию. Без кредов/сессии
    # коннектор telegram_mtproto тихо пропускается (graceful degradation).
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_session_path: str = "data/telegram.session"
    # Закрытые/приватные каналы: инвайт-ссылки (+HASH / joinchat/HASH) и/или @username,
    # через запятую. Пусто — MTProto-источник отключён.
    telegram_private_channels: str = ""
    # Сколько последних сообщений тянуть с каждого приватного канала за опрос.
    telegram_mtproto_limit: int = 50

    # --- Бэктест ---
    # Транзакционная издержка (комиссия + проскальзывание) за ОДНУ сторону сделки, базисные
    # пункты (10 б.п. = 0.1%). У завершённой сделки набегает round-trip = 2×. Делает оценку
    # доходности честной. 5 б.п. ≈ 0.05% — комиссия MOEX + умеренное проскальзывание.
    backtest_cost_bps: float = 5.0

    # --- Прочее ---
    log_level: str = "INFO"
    history_days: int = Field(default=365, description="Глубина первичной загрузки котировок, дней")
    # Компоненты, чья деградация ОЖИДАЕМА на этом узле и НЕ должна слать health-алерт (CSV).
    # Фаза 2 / Raspberry Pi: scheduler на Pi НАМЕРЕННО без эмбеддера (fastembed→ноут) и без
    # LLM (Ollama→ноут) — иначе health шлёт ложные critical «Деградация каскада» каждый день.
    # Деградация всё равно логируется (health_degraded), но алерт по этим компонентам молчит.
    # Пусто (ноут) — алертит обо всех. Пример (Pi): GEO_HEALTH_EXPECTED_OFFLINE=embedder,llm
    health_expected_offline: str = ""

    @property
    def telegram_chat_ids(self) -> list[str]:
        """Список получателей Telegram (поддержка нескольких id через запятую)."""
        if not self.telegram_chat_id:
            return []
        return [c.strip() for c in self.telegram_chat_id.split(",") if c.strip()]

    @property
    def health_expected_offline_set(self) -> frozenset[str]:
        """Компоненты, чья деградация ожидаема и не алертится (через запятую)."""
        return frozenset(
            c.strip() for c in self.health_expected_offline.split(",") if c.strip()
        )

    @property
    def require_impact_type_set(self) -> frozenset[str]:
        """Типы событий, требующие asset-impact для new_event-алерта (через запятую)."""
        return frozenset(
            t.strip().lower()
            for t in self.alert_require_impact_types.split(",")
            if t.strip()
        )

    @property
    def alert_exclude_kind_set(self) -> frozenset[str]:
        """Виды активов (Asset.kind), исключённые из алертов (MMF: fund)."""
        return frozenset(
            k.strip().lower() for k in self.alert_exclude_kinds.split(",") if k.strip()
        )

    @property
    def alert_exclude_source_set(self) -> frozenset[str]:
        """Источники (source_ref), чьи новости не порождают алерты (сигнальные каналы)."""
        return frozenset(
            s.strip() for s in self.alert_exclude_sources.split(",") if s.strip()
        )

    @property
    def database_url(self) -> str:
        """DSN для SQLAlchemy (драйвер psycopg v3)."""
        return (
            f"postgresql+psycopg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache
def get_settings() -> Settings:
    """Возвращает кэшированный экземпляр настроек."""
    return Settings()
