# Storage модуль

Слой хранения данных (PostgreSQL) и ORM модели.

## Структура

### Компоненты
- **db.py** - управление сессиями БД, connection pooling
- **models.py** - SQLAlchemy модели (Article, Asset, Event, Alert и др.)
- **repositories.py** - репозитории для работы с сущностями
- **retention.py** - политика удержания данных
- **seed.py** - начальная загрузка справочников

### Миграции

Alembic миграции находятся в `alembic/versions/`:

```bash
# Создание миграции
PYTHONPATH=. .venv/bin/geo db revision --autogenerate -m "description"

# Применение миграций
PYTHONPATH=. .venv/bin/geo db upgrade

# Откат миграции
PYTHONPATH=. .venv/bin/geo db downgrade
```

### Инициализация

```bash
# Полная инициализация (миграции + сид)
PYTHONPATH=. .venv/bin/geo db upgrade
PYTHONPATH=. .venv/bin/geo db seed
```

## Модели данных

**Основные сущности:**
- `Article` - новостная статья
- `Asset` - финансовый актив
- `Event` - значимое событие
- `Alert` - алерт
- `ArticleEntity` - связь статья-сущность
- `FuturesDecision` - решение фьючерсного трейдера
- `FuturesPaper` - результат paper trading

## Использование

```python
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.repositories import ArticleRepository

with session_scope() as session:
    repo = ArticleRepository(session)
    articles = repo.recent(hours=24)
```

## Конфигурация

Переменные окружения:
- `GEO_DB_HOST` - хост БД
- `GEO_DB_PORT` - порт БД
- `GEO_DB_NAME` - имя БД
- `GEO_DB_USER` - пользователь БД
- `GEO_DB_PASSWORD` - пароль БД

## Зависимости

- sqlalchemy
- alembic
- psycopg2 (или psycopg3)
