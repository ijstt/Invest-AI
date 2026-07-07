# NLP модуль

Обработка естественного языка для анализа финансовых новостей и генерации сигналов.

## Компоненты

### Классификация
- **sentiment.py** - анализ тональности новостей (positive/negative/neutral)
- **aspect.py** - аспектная классификация (финансовые аспекты в тексте)
- **significance.py** - оценка значимости новостей (low/medium/high)
- **temporal.py** - временная классификация (горизонт влияния новости)
- **numeric.py** - извлечение числовых данных из текстов
- **classify.py** - общий интерфейс для классификаторов
- **_seqcls.py** - базовый класс для sequence classification моделей

### NER и Entity Linking
- **ner.py** - Named Entity Recognition (извлечение сущностей)
- **entity_linking.py** - связывание сущностей с активами в БД

### Эмбеддинги и семантика
- **embeddings.py** - генерация эмбеддингов для семантического поиска
- **semantic.py** - семантический поиск и кластеризация

### LLM интеграция
- **llm.py** - интеграция с локальными LLM (Ollama/Qwen)
- **rumor.py** - анализ слухов и непроверенной информации
- **forecast.py** - генерация прогнозов на основе новостей
- **fundamentals.py** - анализ фундаментальных факторов

### Датасеты
- **dataset.py** - загрузка и подготовка датасетов для обучения
- **themes.py** - тематическое моделирование

## Модели

Используются предобученные модели ruBERT с возможностью дообучения LoRA-адаптерами:

- **Базовая модель**: `blanchefort/rubert-base-cased-sentiment` (сентимент)
- **Дообучение**: `scripts/train_lora.py` - обучение LoRA-адаптеров на доменных данных

## Использование

```python
from geoanalytics.nlp import sentiment, aspect, significance

# Анализ тональности
result = sentiment.classify("Компания сообщила о росте прибыли")
# -> {"label": "positive", "score": 0.89}

# Аспектная классификация
aspects = aspect.extract("Снижение ключевой ставки ЦБ")
# -> [{"aspect": "monetary_policy", "sentiment": "negative"}]

# Оценка значимости
sig = significance.classify("Отчет о прибылях и убытках за Q4")
# -> {"label": "high", "score": 0.92}
```

## Обучение

Для обучения адаптеров:

```bash
# Сентимент
python scripts/train_lora.py --task sentiment \
    --dataset data/sentiment_dataset.jsonl \
    --output data/adapters/sentiment-lora

# Значимость
python scripts/train_lora.py --task significance \
    --dataset data/significance_dataset.jsonl \
    --output data/adapters/significance-lora
```

## Зависимости

- transformers
- torch
- sentence-transformers (для эмбеддингов)
- fastembed (опционально, для легких эмбеддингов)
