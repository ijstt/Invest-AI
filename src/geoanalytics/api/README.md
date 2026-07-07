# API модуль

REST API для взаимодействия с системой геоаналитики.

## Эндпоинты

### Health & Status
- `GET /health` - проверка работоспособности
- `GET /sources` - список доступных источников данных
- `GET /assets` - список активов

### News & Analytics
- `GET /news` - сводка по новостям (макро, тональность, заголовки)
- `GET /asset/{ticker}` - аналитический отчёт по активу
- `GET /events` - последние значимые события
- `GET /alerts` - сработавшие алерты

### Backtesting
- `GET /backtest/{ticker}` - бэктест стратегии по истории

### Web Interface
- `GET /` - веб-дашборд
- `GET /graph` - граф связей активов

## Запуск

```bash
# Разработка
PYTHONPATH=. .venv/bin/geo serve

# Production (через systemd)
systemctl --user start geo-dashboard
```

## Конфигурация

Переменные окружения:
- `GEO_DASHBOARD_PORT` - порт дашборда (по умолчанию 8800)
- `GEO_DB_HOST` - хост БД
- `GEO_DB_PORT` - порт БД

## Примеры запросов

```bash
# Сводка по новостям
curl http://localhost:8800/news?hours=24

# Отчёт по активу
curl http://localhost:8800/asset/SBER

# Бэктест
curl http://localhost:8800/backtest/SBER?strategy=sma_cross
```

## Зависимости

- fastapi
- uvicorn
- pydantic
