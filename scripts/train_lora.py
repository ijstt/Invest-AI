#!/usr/bin/env python
"""Дообучение encoder-модели LoRA-адаптером под домен (M4).

Обучает лёгкий LoRA-адаптер поверх ruBERT-классификатора (sequence
classification) на датасете, собранном `scripts/build_dataset.py`. LoRA меняет
доли процента весов, поэтому реально обучаемо даже на CPU/слабом железе (хотя на
GPU кратно быстрее).

Тяжёлые зависимости (transformers/peft/datasets/accelerate) ставятся отдельно:
    pip install -e ".[train]"

Примеры:
    # Сентимент (база — та же ruBERT, что в проде; адаптер сохраняет 3 метки):
    python scripts/train_lora.py --task sentiment \
        --dataset data/sentiment_dataset.jsonl \
        --output data/adapters/sentiment-lora

    # Классификатор событий (с нуля по меткам датасета):
    python scripts/train_lora.py --task events \
        --base cointegrated/rubert-tiny2 \
        --dataset data/events_dataset.jsonl \
        --output data/adapters/events-lora

    # Классификатор значимости (метки low/medium/high):
    python scripts/train_lora.py --task significance \
        --dataset data/significance_dataset.jsonl \
        --output data/adapters/significance-lora

Включить адаптеры в проде (каждый подключается независимо, иначе — фолбэк):
    export GEO_SENTIMENT_ADAPTER_PATH=data/adapters/sentiment-lora
    export GEO_EVENT_ADAPTER_PATH=data/adapters/events-lora
    export GEO_SIGNIFICANCE_ADAPTER_PATH=data/adapters/significance-lora
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Запуск как `python scripts/train_lora.py` — добавляем корень репозитория в путь,
# чтобы импортировался пакет `geoanalytics` (src/) при запуске не из корня.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

# База по умолчанию для каждой задачи.
DEFAULT_BASE = {
    "sentiment": "blanchefort/rubert-base-cased-sentiment",
    "events": "cointegrated/rubert-tiny2",
    "significance": "cointegrated/rubert-tiny2",
    "temporal": "cointegrated/rubert-tiny2",
}


def _require_training_deps():
    """Импортирует тяжёлые зависимости, иначе — понятная подсказка и выход."""
    try:
        import datasets  # noqa: F401
        import peft  # noqa: F401
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError as exc:
        sys.exit(
            f"Не хватает зависимостей для обучения ({exc.name}). "
            'Установите: pip install -e ".[train]"'
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="LoRA-дообучение классификатора (M4).")
    parser.add_argument("--task", choices=sorted(DEFAULT_BASE), required=True)
    parser.add_argument("--dataset", required=True, help="JSONL: {text,label} на строку.")
    parser.add_argument("--output", required=True, help="Каталог для адаптера.")
    parser.add_argument("--base", default=None, help="Базовая модель (иначе дефолт задачи).")
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--max-length", type=int, default=256)
    parser.add_argument("--lora-r", type=int, default=8, help="Ранг LoRA (ёмкость).")
    parser.add_argument("--lora-targets", default="query,value",
                        help="Модули под LoRA через запятую (напр. query,key,value).")
    parser.add_argument("--class-weights", dest="class_weights", action="store_true",
                        default=True, help="Взвешивать loss обратной частотой класса (по умолч.).")
    parser.add_argument("--no-class-weights", dest="class_weights", action="store_false",
                        help="Отключить веса классов: инверс-частота даёт тривиальный оптимум "
                             "(константный выход), если frozen-энкодер плохо разделяет классы.")
    parser.add_argument("--train-pooler", action="store_true",
                        help="Обучать pooler (агрегацию [CLS]) — нужно, когда метки учителя "
                             "расходятся с родным сентиментом базы и признаки надо адаптировать.")
    parser.add_argument("--full-finetune", action="store_true",
                        help="Полный fine-tune энкодера вместо LoRA (сохраняет модель целиком). "
                             "Нужен для дистилляции LLM-учителя: LoRA-поверх-frozen упирается в "
                             "потолок, т.к. признаки базы расходятся с метками учителя. "
                             "Используйте меньший --lr (напр. 3e-5) и небольшую базу "
                             "(rubert-tiny2 — быстро на CPU).")
    args = parser.parse_args()

    _require_training_deps()
    import numpy as np
    import torch
    from datasets import ClassLabel, Dataset
    from peft import LoraConfig, TaskType, get_peft_model
    from transformers import (
        AutoModelForSequenceClassification,
        AutoTokenizer,
        DataCollatorWithPadding,
        Trainer,
        TrainingArguments,
    )

    from geoanalytics.nlp.dataset import dedup_normalized, read_jsonl

    class WeightedTrainer(Trainer):
        """Trainer со взвешенным CrossEntropyLoss — против дисбаланса классов.

        Веса = обратная частота класса (редкий класс получает больший вес), иначе
        миноритарный класс (напр. ~редкий positive в сентименте) модель игнорирует.
        """

        def __init__(self, *args, class_weights=None, **kwargs):
            super().__init__(*args, **kwargs)
            self._class_weights = class_weights

        def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
            labels = inputs.pop("labels")
            outputs = model(**inputs)
            weight = (
                self._class_weights.to(outputs.logits.device)
                if self._class_weights is not None
                else None
            )
            loss = torch.nn.functional.cross_entropy(outputs.logits, labels, weight=weight)
            return (loss, outputs) if return_outputs else loss

    base = args.base or DEFAULT_BASE[args.task]
    records = read_jsonl(args.dataset)
    if not records:
        sys.exit(f"Пустой датасет: {args.dataset}. Сначала scripts/build_dataset.py")

    # Дедуп near-дублей ДО train/test split: одна новость от разных лент с косметическими
    # отличиями текста, разошедшись по train и eval, завышает eval-метрику (утечка). Точный
    # dedup в llm_label это не ловит — нормализуем текст. См. nlp.dataset.dedup_normalized.
    n_before = len(records)
    records = dedup_normalized(records)
    if len(records) < n_before:
        print(f"Дедуп near-дублей: {n_before} → {len(records)} (-{n_before - len(records)})")

    # Карта меток (стабильный порядок — сортировка по имени).
    labels = sorted({r["label"] for r in records})
    label2id = {lab: i for i, lab in enumerate(labels)}
    id2label = {i: lab for lab, i in label2id.items()}
    print(f"Меток: {len(labels)} → {labels}; примеров: {len(records)}")

    tokenizer = AutoTokenizer.from_pretrained(base)
    model = AutoModelForSequenceClassification.from_pretrained(
        base, num_labels=len(labels), id2label=id2label, label2id=label2id,
        ignore_mismatched_sizes=True,
    )

    # Сброс головы классификации. Если у базы УЖЕ есть обученная голова (как у
    # blanchefort sentiment), её веса соответствуют РОДНОМУ порядку меток базы
    # (neutral/positive/negative), а мы переопределяем порядок через sorted(labels)
    # (negative/neutral/positive). Без сброса обученная голова «тянет» в свой
    # порядок. Сброс заставляет голову учить наш порядок с нуля. Для rubert-tiny2
    # голова и так случайна — сброс безвреден.
    head = getattr(model, "classifier", None) or getattr(model, "score", None)
    if isinstance(head, torch.nn.Linear):
        head.weight.data.normal_(mean=0.0, std=0.02)
        if head.bias is not None:
            head.bias.data.zero_()

    if args.full_finetune:
        # Полный fine-tune: дообучаем ВЕСЬ энкодер, без LoRA. Диагностика показала, что
        # признаки замороженного blanchefort не выражают разметку учителя (Qwen) —
        # линейный пробинг и LoRA-поверх-frozen упирались в macro-F1 ≈ random (0.30),
        # тогда как полный FT небольшого энкодера уверенно учится (loss падает,
        # macro-F1 ~0.68). Модель сохраняется целиком (config + веса), а не как адаптер.
        n_train = sum(p.numel() for p in model.parameters() if p.requires_grad)
        print(f"Полный fine-tune: обучаемых параметров {n_train:,}")
    else:
        # Голову (и опц. pooler) обучаем целиком и сохраняем в адаптер.
        save_modules = ["classifier"] + (["pooler"] if args.train_pooler else [])
        targets = [t.strip() for t in args.lora_targets.split(",") if t.strip()]
        lora = LoraConfig(
            task_type=TaskType.SEQ_CLS, r=args.lora_r, lora_alpha=2 * args.lora_r,
            lora_dropout=0.05, target_modules=targets, modules_to_save=save_modules,
        )
        model = get_peft_model(model, lora)
        model.print_trainable_parameters()

    def _encode(batch: dict) -> dict:
        enc = tokenizer(batch["text"], truncation=True, max_length=args.max_length)
        enc["labels"] = [label2id[lab] for lab in batch["label"]]
        return enc

    # Удаляем исходные строковые колонки (text/label) — иначе data collator пытается
    # тензоризовать строку `label` и падает. Остаются input_ids/attention_mask/labels.
    ds = Dataset.from_list(records).map(_encode, batched=True, remove_columns=["text", "label"])
    # Делаем "labels" типом ClassLabel, чтобы сплит был стратифицированным (редкий класс
    # попадает и в train, и в eval). При совсем малом классе (<2) стратификация невозможна —
    # откатываемся на обычный сплит.
    ds = ds.cast_column("labels", ClassLabel(num_classes=len(labels), names=labels))
    try:
        split = ds.train_test_split(test_size=0.1, seed=42, stratify_by_column="labels")
    except Exception as exc:  # noqa: BLE001 — малый класс не делится стратифицированно
        print(f"Стратификация не удалась ({exc}); обычный сплит.")
        split = ds.train_test_split(test_size=0.1, seed=42)

    # Веса классов по частоте в train-сплите (обратная частота, нормированная).
    train_label_ids = list(split["train"]["labels"])
    counts = np.bincount(train_label_ids, minlength=len(labels)).astype(np.float64)
    counts[counts == 0] = 1.0  # защита от деления на ноль
    print(f"Частоты классов (train): {dict(zip(labels, counts.astype(int), strict=False))}")
    if args.class_weights:
        class_weights = torch.tensor(
            (counts.sum() / (len(labels) * counts)), dtype=torch.float32
        )
        print(f"Веса классов: {dict(zip(labels, class_weights.tolist(), strict=False))}")
    else:
        class_weights = None
        print("Веса классов: отключены (--no-class-weights).")

    def _metrics(eval_pred) -> dict:
        logits, gold = eval_pred
        preds = np.argmax(logits, axis=1)
        # Macro-F1: среднее F1 по классам (при дисбалансе важнее accuracy).
        f1s = []
        for c in range(len(labels)):
            tp = int(np.sum((preds == c) & (gold == c)))
            fp = int(np.sum((preds == c) & (gold != c)))
            fn = int(np.sum((preds != c) & (gold == c)))
            prec = tp / (tp + fp) if tp + fp else 0.0
            rec = tp / (tp + fn) if tp + fn else 0.0
            f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
        out = {
            "accuracy": float((preds == gold).mean()),
            "macro_f1": float(np.mean(f1s)),
        }
        # Per-class F1 — видно качество КАЖДОГО класса (важно для редких: earnings/dividends).
        for c, lab in enumerate(labels):
            out[f"f1_{lab}"] = float(f1s[c])
        return out

    training_args = TrainingArguments(
        output_dir=str(Path(args.output) / "_trainer"),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        learning_rate=args.lr,
        eval_strategy="epoch",
        save_strategy="no",
        logging_steps=20,
        report_to=[],
    )
    trainer = WeightedTrainer(
        model=model, args=training_args,
        train_dataset=split["train"], eval_dataset=split["test"],
        processing_class=tokenizer, data_collator=DataCollatorWithPadding(tokenizer),
        compute_metrics=_metrics, class_weights=class_weights,
    )
    trainer.train()
    print("Оценка:", trainer.evaluate())

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out)          # только веса адаптера (немного МБ)
    tokenizer.save_pretrained(out)
    (out / "labels.json").write_text(
        json.dumps({"labels": labels, "base": base}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Адаптер сохранён: {out}")


if __name__ == "__main__":
    main()
