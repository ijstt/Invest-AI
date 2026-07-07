# Futrader модуль

Модуль алгоритмической торговли фьючерсами с машинным обучением.

## Архитектура

### Основные компоненты

**Управление позицией:**
- **session.py** - сессия торговли (состояние, PnL, открытые позиции)
- **policy.py** - торговая политика (правила входа/выхода, риск-менеджмент)
- **execution.py** - исполнение ордеров
- **sizing.py** - определение размера позиции
- **exits.py** - стратегии выхода

**Фичи и сигналы:**
- **features.py** - генерация признаков из рыночных данных
- **signals.py** - генерация торговых сигналов
- **instrument_features.py** - специфические признаки для инструментов
- **underlying.py** - работа с базовым активом

**Риск-менеджмент:**
- **risk_limits.py** - лимиты риска
- **portfolio_risk.py** - риск на уровне портфеля
- **conviction.py** - оценка уверенности в сигнале
- **depth.py** - анализ стакана

**Оценка и бэктестинг:**
- **evaluation.py** - оценка стратегий
- **paper.py** - paper trading (симуляция)
- **track.py** - отслеживание результатов
- **monitoring.py** - мониторинг в реальном времени

**Модели ML:**
- **decisions.py** - ML-модель для принятия решений
- **labeling.py** - разметка данных для обучения
- **data.py** - подготовка данных

## Использование

```python
from geoanalytics.futrader import session, policy, execution

# Создание сессии
sess = session.FuturesSession(
    asset_code="Si",  # фьючерс на доллар/рубль
    interval="5m"
)

# Получение сигнала
signal = policy.generate_signal(sess)
if signal.action == "BUY":
    # Определение размера
    size = sizing.calculate_size(sess, signal)
    # Исполнение
    execution.execute_order(sess, "BUY", size)
```

## Обучение моделей

```bash
# Подготовка данных
python scripts/build_futures_dataset.py

# Обучение (через orchestration/futrader_runner.py)
PYTHONPATH=. .venv/bin/geo futrader train --asset Si --interval 5m
```

## Мониторинг

```bash
# Статус торговой сессии
curl http://localhost:8800/api/futrader/status

# Логи
journalctl --user -u geo-futrader -f
```

## Риск-менеджмент

Система включает многоуровневый контроль риска:
- Лимиты на размер позиции
- Stop-loss на основе волатильности
- Лимиты на просадку портфеля
- PSI-мониторинг для дрейфа признаков

## Зависимости

- pandas, numpy (обработка данных)
- scikit-learn (ML-модели)
- sqlalchemy (хранение результатов)
