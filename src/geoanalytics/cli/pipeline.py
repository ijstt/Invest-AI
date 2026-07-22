"""Команды пайплайна: инжест, обработка сырых данных и NLP переразметка."""

from __future__ import annotations

import typer
from rich.table import Table

from geoanalytics.cli.common import app, console


@app.command()
def sources() -> None:
    """Показать доступные источники данных."""
    from geoanalytics.connectors import all_connectors

    table = Table(title="Источники")
    table.add_column("Имя")
    table.add_column("Тип")
    for c in all_connectors():
        table.add_row(c.name, str(c.kind))
    console.print(table)


@app.command()
def ingest(
    source: str | None = typer.Option(
        None, "--source", "-s", help="Имя источника; без него — все источники."
    ),
) -> None:
    """Собрать свежие данные из источников в raw-слой."""
    from geoanalytics.connectors.service import ingest_all, ingest_source

    results = [ingest_source(source)] if source else ingest_all()
    table = Table(title="Результат ингеста")
    table.add_column("Источник")
    table.add_column("Получено", justify="right")
    table.add_column("Новых", justify="right")
    table.add_column("Ошибки", justify="right")
    for r in results:
        table.add_row(r.source, str(r.fetched), str(r.stored), str(r.errors))
    console.print(table)


@app.command(name="news-backfill")
def news_backfill(
    since: str = typer.Option(..., "--since", help="Нижняя граница даты YYYY-MM-DD (вкл.)."),
    until: str | None = typer.Option(
        None, "--until", help="Верхняя граница YYYY-MM-DD (искл.); без неё — сейчас."),
    channel: str | None = typer.Option(
        None, "--channel", "-c",
        help="Канал(ы) через запятую (@user / t.me/+invite); без них — из настроек."),
    max_per_channel: int = typer.Option(
        0, "--max", help="Лимит постов на канал (0 — без лимита)."),
) -> None:
    """Историч. бэкфилл новостей из Telegram (MTProto) по окну дат → raw-слой.

    Требует завершённого логина (scripts/telegram_login.py). После — прогоните
    `geo process` и `geo outcomes`: исходы разметятся сразу (цены уже бэкфиллены)."""
    from config.settings import get_settings
    from geoanalytics.connectors.service import store_items
    from geoanalytics.connectors.telegram_mtproto import (
        backfill_channels,
        parse_backfill_window,
        parse_private_channels,
    )

    raw_channels = channel if channel is not None else (
        get_settings().telegram_private_channels or "")
    refs = parse_private_channels(raw_channels)
    if not refs:
        console.print("[yellow]Нет валидных каналов (--channel или настройки).[/]")
        raise typer.Exit(1)
    since_dt, until_dt = parse_backfill_window(since, until)

    console.print(f"Бэкфилл {len(refs)} канал(ов) за "
                  f"{since_dt:%Y-%m-%d}…{until_dt:%Y-%m-%d}…")
    items = backfill_channels(refs, since_dt, until_dt, max_per_channel)
    result = store_items(items, source="telegram_mtproto")

    table = Table(title="Результат бэкфилла новостей")
    table.add_column("Источник")
    table.add_column("Собрано", justify="right")
    table.add_column("Новых", justify="right")
    table.add_row(result.source, str(result.fetched), str(result.stored))
    console.print(table)
    if result.fetched == 0:
        console.print("[yellow]0 постов: проверьте авторизацию "
                      "(.venv/bin/python scripts/telegram_login.py).[/]")


@app.command()
def process() -> None:
    """Обработать накопленные сырые документы (NER, сентимент, эмбеддинги, котировки, события)."""
    from geoanalytics.context.events import build_events
    from geoanalytics.processing import process_pending

    r = process_pending()
    events = build_events()
    console.print(
        f"[green]Обработка завершена[/]: новости={r.articles}, котировки={r.prices}, "
        f"макро={r.macro}, fx={r.fx}, события={events}, пропущено={r.skipped}, ошибки={r.errors}"
    )


@app.command()
def relink() -> None:
    """Перелинковать сущности (и эмбеддинги) по уже сохранённым новостям.

    Полезно, если новости были обработаны до установки NLP-моделей (NER/эмбеддер):
    добавляет недостающие связи article↔актив, не пересоздавая статьи.
    """
    from geoanalytics.processing import relink_existing

    r = relink_existing()
    console.print(
        f"[green]Перелинковка завершена[/]: статей={r.articles}, "
        f"новых связей={r.links}, эмбеддингов={r.embeddings}"
    )


@app.command(name="reconcile-impacts")
def reconcile_impacts_cmd() -> None:
    """Свести EventImpact с живыми связями: удалить устаревшие импакты и освежить знаки.

    Настоящий фикс мины устаревших EventImpact (ложные «события не про этот актив»):
    relink/reaspect меняют связи, а импакты исторически не пересоздавались. Команда чистит
    призраков и переотражает direction/magnitude по текущей тональности связей.
    """
    from geoanalytics.context.events import reconcile_impacts
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        r = reconcile_impacts(session)
    console.print(
        f"[green]Сверка импактов завершена[/]: удалено устаревших={r['pruned']}, "
        f"перестроено={r['rebuilt']}"
    )


@app.command()
def rescore(
    what: str = typer.Option(
        "sentiment,significance", "--what", "-w",
        help="Стадии через запятую: sentiment, events, significance "
             "(значимость пересчитывается автоматически при смене тональности/типа).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Посчитать эффект, ничего не записывать."),
    limit: int | None = typer.Option(None, "--limit", "-n", help="Ограничить число статей."),
    batch_size: int = typer.Option(1000, "--batch-size", help="Размер батча (коммит на батч)."),
) -> None:
    """Переразметить уже сохранённые статьи обновлёнными моделями NLP.

    Нужна при смене модели (напр. новый дистиллированный сентимент): исторические статьи
    хранят метки старой модели — команда приводит их в соответствие текущим. Не пересоздаёт
    статьи и не перелинковывает (для связей — `geo relink`).
    """
    from geoanalytics.processing import rescore_existing

    stages = [s.strip() for s in what.split(",") if s.strip()]
    r = rescore_existing(stages, batch_size=batch_size, limit=limit, dry_run=dry_run)

    head = ("[yellow]Пробный прогон (dry-run)[/]" if r.dry_run
            else "[green]Переразметка завершена[/]")
    console.print(
        f"{head}: статей={r.articles}, тональность изменена={r.sentiment_changed}, "
        f"тип события изменён={r.event_changed}, значимость изменена={r.significance_changed}, "
        f"ошибки={r.errors}"
    )
    if r.sentiment_before != r.sentiment_after:
        order = ["negative", "neutral", "positive", "none"]
        keys = [k for k in order if k in (r.sentiment_before | r.sentiment_after)]
        table = Table(title="Сдвиг тональности (до → после)")
        table.add_column("Метка")
        table.add_column("До", justify="right")
        table.add_column("После", justify="right")
        for k in keys:
            table.add_row(k, str(r.sentiment_before.get(k, 0)), str(r.sentiment_after.get(k, 0)))
        console.print(table)


@app.command()
def prune(
    dry_run: bool = typer.Option(False, "--dry-run", help="Только показать, ничего не удалять."),
) -> None:
    """Удалить новости старше их TTL (срок хранения растёт со значимостью)."""
    from geoanalytics.storage.retention import prune as run_prune

    r = run_prune(dry_run=dry_run)
    verb = "к удалению" if dry_run else "удалено"
    console.print(
        f"[green]Ретеншн[/]: {verb} новостей={r.articles}, сырых документов={r.raw_documents}"
    )


@app.command()
def reaspect(
    limit: int | None = typer.Option(None, "--limit", "-n", help="Ограничить число связей."),
) -> None:
    """Переразметить связи статья↔актив aspect-моделями F1/F2 (после их деплоя)."""
    from geoanalytics.processing import reaspect_existing

    r = reaspect_existing(limit=limit)
    console.print(
        f"[green]Reaspect[/]: связей={r.links}, тональность изменена={r.sentiment_changed}, "
        f"салиентность проставлена={r.salient_set}, ошибки={r.errors}"
    )


@app.command()
def retemporal(
    limit: int | None = typer.Option(None, "--limit", "-n", help="Ограничить число статей."),
) -> None:
    """Переразметить статьи temporal-моделью F3 (статус + дата события)."""
    from geoanalytics.processing import retemporal_existing

    r = retemporal_existing(limit=limit)
    console.print(
        f"[green]Retemporal[/]: статей={r.articles}, статус проставлен={r.status_set}, "
        f"дата события={r.date_set}, ошибки={r.errors}"
    )


@app.command()
def reprocess(
    limit: int | None = typer.Option(None, "--limit", "-n",
                                     help="Ограничить число переоткрываемых доков."),
    process: bool = typer.Option(True, "--process/--no-process",
                                 help="Сразу прогнать process после переоткрытия."),
) -> None:
    """Переоткрыть пропущенные новости на обработку (Б4): processed без статьи → заново.

    Полезно после апгрейда моделей или понижения порога значимости. Дедуп БД и шумовой
    фильтр снова отсеют настоящие дубли/мусор."""
    from geoanalytics.processing import process_pending, reprocess_skipped

    r = reprocess_skipped(limit=limit)
    console.print(f"[green]Reprocess[/]: переоткрыто={r.reopened}")
    if process and r.reopened:
        p = process_pending()
        console.print(f"  обработано: статей={p.articles}, отложено={p.deferred}, "
                      f"пропущено={p.skipped}, дубли={p.duplicates}")


@app.command()
def refactuality(
    limit: int | None = typer.Option(None, "--limit", "-n", help="Ограничить число статей."),
) -> None:
    """Переразметить фактологичность F4 (fact/rumor/opinion) у старых статей."""
    from geoanalytics.processing import refactuality_existing

    r = refactuality_existing(limit=limit)
    labels = ", ".join(f"{k}={v}" for k, v in sorted(r.by_label.items())) or "—"
    console.print(
        f"[green]Refactuality[/]: статей={r.articles}, проставлено={r.set_count} "
        f"({labels}), ошибки={r.errors}"
    )


@app.command()
def renumeric(
    limit: int | None = typer.Option(None, "--limit", "-n", help="Ограничить число статей."),
) -> None:
    """Извлечь числовые факты F5 (дивиденд/ставка/сумма сделки) из старых статей."""
    from geoanalytics.processing import renumeric_existing

    r = renumeric_existing(limit=limit)
    kinds = ", ".join(f"{k}={v}" for k, v in sorted(r.by_kind.items())) or "—"
    console.print(
        f"[green]Renumeric[/]: статей={r.articles}, новых фактов={r.facts} "
        f"({kinds}), ошибки={r.errors}"
    )


@app.command()
def reforecast(
    limit: int | None = typer.Option(None, "--limit", "-n", help="Ограничить число статей."),
) -> None:
    """F10: разметить старые брокерские статьи — is_forecast + наполнить forecasts."""
    from geoanalytics.processing import reforecast_existing

    r = reforecast_existing(limit=limit)
    console.print(
        f"[green]Reforecast[/]: статей={r.articles}, помечено={r.marked}, "
        f"прогнозов={r.forecasts}, ошибки={r.errors}"
    )


@app.command()
def pipeline(
    source: str | None = typer.Option(None, "--source", "-s", help="Только один источник."),
) -> None:
    """Полный цикл: собрать данные и сразу обработать их."""
    from geoanalytics.connectors.service import ingest_all, ingest_source
    from geoanalytics.context.events import build_events
    from geoanalytics.processing import process_pending

    results = [ingest_source(source)] if source else ingest_all()
    stored = sum(r.stored for r in results)
    console.print(f"Собрано новых документов: {stored}")
    r = process_pending()
    events = build_events()
    console.print(
        f"[green]Готово[/]: новости={r.articles}, котировки={r.prices}, "
        f"макро={r.macro}, fx={r.fx}, события={events}, ошибки={r.errors}"
    )
