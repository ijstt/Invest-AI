"""Тесты сборки обучающих датасетов M4 (чистая разметка, без БД и моделей)."""

from __future__ import annotations

from geoanalytics.nlp.dataset import (
    build_event_records,
    build_sentiment_records,
    build_significance_records,
    dedup,
    dedup_normalized,
    label_distribution,
    read_jsonl,
    write_jsonl,
)

# Серебряные строки «как из БД»: title/text/sentiment/sentiment_score/event_type.
_ROWS = [
    {"title": "Прибыль выросла", "text": "рекорд", "sentiment": "positive",
     "sentiment_score": 0.8, "event_type": "earnings"},
    {"title": "Обвал и убытки", "text": "", "sentiment": "negative",
     "sentiment_score": -0.6, "event_type": "other"},
    {"title": "Слабый сигнал", "text": "", "sentiment": "positive",
     "sentiment_score": 0.1, "event_type": "other"},   # ниже порога → отбрасывается
    {"title": "Обычная новость", "text": "", "sentiment": "neutral",
     "sentiment_score": 0.0, "event_type": "macro"},
]


def test_sentiment_records_filter_by_confidence():
    recs = build_sentiment_records(_ROWS, min_confidence=0.3)
    # Остаются: уверенный позитив, уверенный негатив, уверенный нейтрал (score≈0).
    labels = sorted(r["label"] for r in recs)
    assert labels == ["negative", "neutral", "positive"]
    # Слабый позитив (0.1) исключён.
    assert all("Слабый сигнал" not in r["text"] for r in recs)


def test_sentiment_record_text_combines_title_and_body():
    recs = build_sentiment_records(_ROWS[:1], min_confidence=0.3)
    assert recs[0]["text"] == "Прибыль выросла. рекорд"


def test_event_records_drop_other():
    recs = build_event_records(_ROWS)
    labels = sorted(r["label"] for r in recs)
    assert labels == ["earnings", "macro"]  # 'other' исключён по умолчанию


def test_event_records_keep_other_when_requested():
    recs = build_event_records(_ROWS, drop_other=False)
    assert any(r["label"] == "other" for r in recs)


def test_significance_records_buckets():
    rows = [
        {"title": "Очень важно", "text": "", "significance": 0.9},   # high
        {"title": "Средне", "text": "", "significance": 0.5},        # medium
        {"title": "Шум", "text": "", "significance": 0.1},           # low
        {"title": "Без оценки", "text": "", "significance": None},   # пропуск
    ]
    recs = build_significance_records(rows)
    assert label_distribution(recs) == {"high": 1, "low": 1, "medium": 1}
    assert all("Без оценки" not in r["text"] for r in recs)


def test_dedup_by_text():
    recs = [{"text": "a", "label": "x"}, {"text": "a", "label": "y"},
            {"text": "b", "label": "x"}]
    assert dedup(recs) == [{"text": "a", "label": "x"}, {"text": "b", "label": "x"}]


def test_dedup_normalized_catches_cosmetic_near_duplicates():
    """near-дубли (HTML-сущности, регистр, пунктуация) точный dedup пропускает, а
    dedup_normalized схлопывает — сохраняя первое вхождение и его метку."""
    recs = [
        {"text": "Атаки&nbsp;БПЛА на регион", "label": "high"},
        {"text": "атаки бпла на регион!!!", "label": "low"},  # тот же норм-текст → дубль
        {"text": "Другая новость", "label": "medium"},
    ]
    assert dedup(recs) == recs  # точный dedup НЕ ловит (строки различны)
    out = dedup_normalized(recs)
    assert out == [{"text": "Атаки&nbsp;БПЛА на регион", "label": "high"},
                   {"text": "Другая новость", "label": "medium"}]


def test_label_distribution():
    recs = [{"text": "a", "label": "pos"}, {"text": "b", "label": "pos"},
            {"text": "c", "label": "neg"}]
    assert label_distribution(recs) == {"neg": 1, "pos": 2}


def test_jsonl_roundtrip(tmp_path):
    recs = [{"text": "Привет мир", "label": "positive"}]
    path = tmp_path / "ds.jsonl"
    assert write_jsonl(recs, path) == 1
    assert read_jsonl(path) == recs


def test_settings_have_adapter_fields():
    """Пути моделей присутствуют и по умолчанию выключены (фолбэк).

    Проверяем именно дефолты полей, поэтому игнорируем локальный .env (`_env_file=None`):
    в проде GEO_SENTIMENT_ADAPTER_PATH может быть задан (подключённая модель M7), и тест
    не должен от этого падать.
    """
    from config.settings import Settings

    s = Settings(_env_file=None)
    assert s.sentiment_adapter_path is None
    assert s.event_adapter_path is None
    assert s.significance_adapter_path is None


def test_build_market_significance_records_threshold():
    from geoanalytics.nlp.dataset import build_market_significance_records

    rows = [
        {"title": "Сбер взлетел", "text": "детали", "impact": 2.5},
        {"title": "Тихая новость", "text": "детали", "impact": 0.3},
        {"title": "Падение", "text": "детали", "impact": -1.7},  # модуль ≥ порога
        {"title": "Без исхода", "text": "детали", "impact": None},
    ]
    recs = build_market_significance_records(rows, threshold_pct=1.0)
    assert [r["label"] for r in recs] == ["moved", "flat", "moved"]


def test_time_split_keeps_chronology():
    from geoanalytics.nlp.dataset import time_split

    recs = [{"text": str(i), "label": "x"} for i in range(10)]
    train, eval_ = time_split(recs, eval_frac=0.2)
    assert len(train) == 8 and len(eval_) == 2
    assert eval_[0]["text"] == "8"  # eval — строго ПОСЛЕДНИЕ по времени


def test_time_split_empty():
    from geoanalytics.nlp.dataset import time_split

    assert time_split([], eval_frac=0.2) == ([], [])


def test_market_labels_mapped_in_significance_buckets():
    """Метки рыночного золота (E3) маппятся в значение значимости: moved проходит
    гейт алертов 0.6, flat — нет (как high/low у LLM-схемы)."""
    from geoanalytics.nlp.significance import _BUCKET_VALUE

    assert _BUCKET_VALUE["moved"] == _BUCKET_VALUE["high"]
    assert _BUCKET_VALUE["flat"] == _BUCKET_VALUE["low"]
