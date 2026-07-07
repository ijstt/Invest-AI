# Скрипты

Вспомогательные скрипты для обучения, оценки и обработки данных.

## Обучение моделей

### LoRA-адаптеры
- **train_lora.py** - дообучение ruBERT адаптерами на доменных данных

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

## Построение датасетов

- **build_dataset.py** - общий билдер датасетов
- **build_aspect_dataset.py** - датасет для аспектной классификации
- **build_temporal_dataset.py** - датасет для временной классификации
- **build_market_dataset.py** - датасет для рыночной значимости

```bash
python scripts/build_dataset.py --task sentiment --output data/sentiment_dataset.jsonl
```

## Оценка моделей

- **eval_significance.py** - оценка классификатора значимости
- **eval_aspect.py** - оценка аспектной классификации
- **eval_events.py** - оценка классификатора событий
- **eval_temporal.py** - оценка временной классификации
- **eval_numeric.py** - оценка извлечения чисел
- **eval_alerts.py** - оценка системы алертов

```bash
python scripts/eval_significance.py --model data/adapters/significance-lora
```

## LLM разметка

- **llm_label.py** - общая разметка через LLM
- **llm_label_aspect.py** - разметка аспектов
- **llm_label_numeric.py** - разметка чисел
- **llm_label_temporal.py** - разметка времени

```bash
python scripts/llm_label.py --input data/raw_articles.jsonl --output data/labeled.jsonl
```

## Обработка данных

- **dedup_articles.py** - дедупликация статей
- **clean_entity_links.py** - очистка связей сущностей
- **telegram_login.py** - авторизация в Telegram

## Зависимости

Для обучения моделей требуются дополнительные зависимости:
```bash
pip install -e ".[train]"
```

Включает:
- transformers
- peft
- datasets
- accelerate
