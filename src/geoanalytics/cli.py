"""CLI на Typer — точка входа `geo`.

Команды:
  geo sources              — список доступных источников
  geo ingest [--source]    — собрать данные (один источник или все)
  geo news                 — сводка «что по новостям»
  geo asset TICKER         — аналитика по активу
  geo db upgrade           — применить миграции БД
  geo run-scheduler        — периодический сбор (заготовка)
  geo run-futrader         — Трек 2: автономный торговый цикл (отдельный процесс, Pi-ready)
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config.settings import get_settings
from geoanalytics.core.logging import configure_logging

app = typer.Typer(
    help="geoanalytics — аналитика экономики и геополитики (рынок РФ).",
    no_args_is_help=True,
)
console = Console()


@app.callback()
def _init() -> None:
    """Глобальная инициализация (логирование) перед любой командой."""
    configure_logging(get_settings().log_level)


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
def news(
    no_llm: bool = typer.Option(False, "--no-llm", help="Не вызывать LLM-сводку."),
    hours: int = typer.Option(24, "--hours", "-h", help="Окно новостей в часах."),
) -> None:
    """Сводка «что по новостям»: ставка, курсы, топ-движения, тональность, заголовки."""
    from geoanalytics.query.news_summary import build_snapshot

    snap = build_snapshot(hours=hours, use_llm=not no_llm)

    # LLM-сводка (если доступна).
    if snap.llm_summary:
        console.print(Panel(snap.llm_summary, title="Сводка"))

    # Макро-блок.
    macro_lines = []
    if snap.key_rate is not None:
        macro_lines.append(f"Ключевая ставка ЦБ: [bold]{snap.key_rate}%[/] ({snap.key_rate_date})")
    for cur, val in snap.fx.items():
        if val is not None:
            macro_lines.append(f"{cur}/RUB: [bold]{val:.2f}[/]")
    console.print(Panel("\n".join(macro_lines) or "нет макро-данных", title="Макро"))

    # Тональность и темы.
    if snap.sentiment_breakdown or snap.top_events:
        sb = snap.sentiment_breakdown
        mood = (f"[green]+{sb.get('positive', 0)}[/] / "
                f"[dim]={sb.get('neutral', 0)}[/] / "
                f"[red]-{sb.get('negative', 0)}[/]")
        events = ", ".join(f"{e} ({n})" for e, n in snap.top_events) or "—"
        console.print(Panel(f"Тональность (поз/нейтр/нег): {mood}\nТемы: {events}",
                            title="Настроение рынка"))

    # Топ-движения.
    if snap.top_gainers or snap.top_losers:
        table = Table(title="Топ-движения (TQBR)")
        table.add_column("Тикер")
        table.add_column("Изм. %", justify="right")
        table.add_column("Цена", justify="right")
        for m in snap.top_gainers:
            table.add_row(f"[green]{m['ticker']}[/]", f"{m['change_pct']:+.2f}", str(m["last"]))
        for m in snap.top_losers:
            table.add_row(f"[red]{m['ticker']}[/]", f"{m['change_pct']:+.2f}", str(m["last"]))
        console.print(table)

    # Заголовки с маркером тональности.
    if snap.headlines:
        marker = {"positive": "[green]▲[/]", "negative": "[red]▼[/]", "neutral": "[dim]■[/]"}
        lines = [f"{marker.get(h.get('sentiment'), ' ')} {h['title']}" for h in snap.headlines]
        console.print(Panel("\n".join(lines), title="Свежие заголовки"))

    if not (macro_lines or snap.top_gainers or snap.headlines):
        console.print("[yellow]Данных пока нет. Сначала: geo pipeline[/]")


@app.command()
def digest(
    send: bool = typer.Option(False, "--send", help="Отправить в Telegram (дедуп по дате)."),
    hours: int = typer.Option(24, "--hours", "-h", help="Окно новостей в часах."),
) -> None:
    """J2: ежедневный дайджест — макро, движения, фон по активам, события, новости."""
    from geoanalytics.query.digest import build_digest_text, send_daily_digest

    if send:
        sent = send_daily_digest(hours=hours)
        console.print("[green]Дайджест отправлен.[/]" if sent
                      else "[yellow]Дайджест уже отправлен сегодня (или нет каналов).[/]")
    else:
        console.print(build_digest_text(hours=hours))


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
def health(
    alert: bool = typer.Option(
        False, "--alert", help="Слать Telegram-алерты о деградации (как делает scheduler)."
    ),
) -> None:
    """Health-check каскада (I4): какие модели живы, какие фолбэки активны."""
    from geoanalytics.health import STATUS_OK, report

    components = report(send_alerts=alert)
    table = Table(title="Health каскада")
    table.add_column("Компонент")
    table.add_column("Статус")
    table.add_column("Детали")
    for c in components:
        color = "green" if c.status == STATUS_OK else "red"
        table.add_row(c.name, f"[{color}]{c.status}[/]", c.detail)
    console.print(table)
    bad = [c for c in components if c.status != STATUS_OK]
    if bad:
        console.print(f"[red]Деградация: {', '.join(c.name for c in bad)}[/]")
        raise typer.Exit(code=1)
    console.print("[green]Все компоненты в норме.[/]")


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
def forecasts(
    ticker: str | None = typer.Option(None, "--ticker", "-t", help="Фильтр по тикеру."),
    limit: int = typer.Option(20, "--limit", "-n", help="Сколько прогнозов показать."),
) -> None:
    """F10: прогнозы брокеров (целевая цена/дивиденд) по активам."""
    from rich.table import Table
    from sqlalchemy import select

    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset
    from geoanalytics.storage.repositories import ForecastRepository

    with session_scope() as session:
        asset_id = None
        if ticker:
            asset = session.scalars(
                select(Asset).where(Asset.ticker == ticker.upper())
            ).first()
            if asset is None:
                console.print(f"[red]Актив {ticker.upper()} не найден[/]")
                raise typer.Exit(1)
            asset_id = asset.id
        rows = ForecastRepository(session).list_forecasts(asset_id=asset_id, limit=limit)
        if not rows:
            console.print("[yellow]Прогнозов нет[/] (запустите `geo reforecast`)")
            return
        table = Table(title="Прогнозы брокеров (F10)")
        for col in ("Тикер", "Вид", "Значение", "Горизонт", "Канал", "Добавлен"):
            table.add_column(col)
        for f, a in rows:
            table.add_row(
                a.ticker, f.kind, f"{f.value:g} {f.unit}",
                f.target_date.isoformat() if f.target_date else "—",
                f.source_channel or "—",
                f.created_at.date().isoformat() if f.created_at else "—",
            )
        console.print(table)


@app.command()
def stories(
    assign: bool = typer.Option(False, "--assign", help="Прогнать кластеризацию (backfill)."),
    hours: int = typer.Option(48, "--hours", "-h", help="Окно для топа сюжетов, часов."),
    limit: int = typer.Option(15, "--limit", "-n", help="Сколько сюжетов показать."),
) -> None:
    """Сюжеты (F6): кластеры статей об одном событии; топ за окно."""
    from geoanalytics.context.stories import assign_stories, top_stories

    if assign:
        r = assign_stories()
        console.print(
            f"[green]Кластеризация[/]: к сюжетам={r.assigned}, новых сюжетов={r.created}, "
            f"ошибки={r.errors}"
        )
    table = Table(title=f"Топ сюжетов за {hours} ч")
    table.add_column("ID", justify="right")
    table.add_column("Статей", justify="right")
    table.add_column("Сюжет")
    table.add_column("Последняя", justify="right")
    for s in top_stories(hours=hours, limit=limit):
        last = s["last_seen_at"].strftime("%m-%d %H:%M") if s["last_seen_at"] else "—"
        table.add_row(str(s["id"]), str(s["n_articles"]), s["title"][:80], last)
    console.print(table)


@app.command()
def calendar(
    sync: bool = typer.Option(False, "--sync",
                              help="Затянуть график ЦБ, отсечки MOEX и smart-lab."),
    days: int = typer.Option(30, "--days", "-d", help="Горизонт списка, дней вперёд."),
) -> None:
    """Календарь событий (H2): заседания ЦБ по ставке, дивидендные отсечки."""
    from geoanalytics.context.calendar import sync_calendar, upcoming_events
    from geoanalytics.storage.db import session_scope

    if sync:
        r = sync_calendar()
        console.print(
            f"[green]Синк календаря[/]: заседаний ЦБ={r.cbr}, отсечек MOEX={r.dividends}, "
            f"будущих smart-lab={r.smartlab}, ошибки={r.errors}"
        )
    table = Table(title=f"События ближайших {days} дн.")
    table.add_column("Дата", justify="right")
    table.add_column("Тикер")
    table.add_column("Событие")
    with session_scope() as session:
        for ev in upcoming_events(session, days_ahead=days):
            table.add_row(ev["event_date"].strftime("%Y-%m-%d"),
                          ev["ticker"] or "—", ev["title"][:80])
    console.print(table)


@app.command()
def outcomes(
    limit: int | None = typer.Option(None, "--limit", "-n", help="Ограничить число пар."),
) -> None:
    """Рыночная авто-разметка новостей (E2): форвардные доходности → news_outcomes."""
    from geoanalytics.analytics.outcomes import label_news_outcomes

    r = label_news_outcomes(limit=limit)
    console.print(
        f"[green]Разметка исходов[/]: записано={r.labeled}, ждут горизонта={r.pending}, "
        f"без истории цен={r.no_history}, ошибки={r.errors}"
    )
    if r.by_asset:
        top = sorted(r.by_asset.items(), key=lambda kv: -kv[1])[:10]
        console.print("По активам: " + ", ".join(f"{t}={n}" for t, n in top))


@app.command("continuous-eval")
def continuous_eval(
    days: int = typer.Option(90, "--days", "-d", help="Окно исходов для оценки."),
    no_alerts: bool = typer.Option(False, "--no-alerts", help="Не слать дрейф-алерт."),
) -> None:
    """Непрерывная оценка значимости против рынка (I2): precision/recall гейта + дрейф.

    Учитель — фактическая реакция цены (news_outcomes). Пишет метрику в eval_runs; при
    деградации относительно трейлинг-базы шлёт алерт model_drift. Обычно — еженедельный джоб."""
    from geoanalytics.analytics.continuous_eval import run_continuous_eval, run_stance_eval
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        s = run_continuous_eval(session, days=days, send_alerts=not no_alerts)
    a = s.agreement
    if not s.recorded:
        console.print(f"[yellow]significance: {s.note or 'прогон не записан'}[/] (n={a.n})")
    else:
        console.print(
            f"[green]significance vs рынок[/] (n={a.n}, помечено={a.n_flagged}, "
            f"двинулось={a.n_moved}): precision={a.precision:.3f}"
            + (f", recall={a.recall:.3f}" if a.recall else ""))
        d = s.drift
        if d.drifted:
            console.print(f"[red]⚠ Дрейф[/]: {d.reason}"
                          + (" · алерт отправлен" if s.alerted else ""))
        elif d.baseline is not None:
            console.print(f"База {d.baseline:.3f} — {d.reason}.")

    # C1: калибровка направленной стойки против реализованного движения рынка (дозрело).
    with session_scope() as session:
        sd = run_stance_eval(session, days=days, send_alerts=not no_alerts)
    sa = sd.agreement
    if not sd.recorded:
        console.print(f"[yellow]стойка: {sd.note or 'прогон не записан'}[/]")
    else:
        console.print(f"[green]стойка vs рынок[/] (направленных={sa.n_flagged}): "
                      f"directional_precision={sa.precision:.3f}"
                      + (f" · [red]⚠ дрейф[/]: {sd.drift.reason}" if sd.drift.drifted else ""))


@app.command("active-learn")
def active_learn(
    task: str = typer.Option("sentiment", "--task", "-t", help="sentiment | significance."),
    threshold: float = typer.Option(0.25, "--threshold", help="Порог уверенности (ниже — берём)."),
    limit: int = typer.Option(30, "--limit", "-n", help="Сколько кандидатов."),
    days: int = typer.Option(30, "--days", "-d", help="Окно статей."),
) -> None:
    """Активное обучение (I5): низкоуверенные предсказания на ручную разметку (у границы решения).

    Берём из хранимых полей (sentiment_score / significance) — новых данных не нужно. Помогает
    наполнять золото там, где модель спорит, а не случайно."""
    from geoanalytics.analytics.active_learning import low_confidence_candidates
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        cands = low_confidence_candidates(session, task=task, threshold=threshold,
                                          limit=limit, days=days)
    if not cands:
        console.print(f"[yellow]Нет низкоуверенных кандидатов ({task}, порог {threshold}).[/]")
        return
    table = Table(title=f"Активное обучение — {task} (уверенность < {threshold})")
    table.add_column("Увер.", justify="right")
    table.add_column("Метка")
    table.add_column("Скор", justify="right")
    table.add_column("Заголовок")
    for c in cands:
        table.add_row(f"{c['confidence']:.3f}", str(c["label"]), f"{c['score']:.3f}",
                      (c["title"] or "")[:70])
    console.print(table)


@app.command("sentiment-index")
def sentiment_index(
    backfill: bool = typer.Option(False, "--backfill", help="Перезаполнить за N дней назад."),
    days: int = typer.Option(30, "--days", "-d", help="Окно бэкфилла (дни)."),
) -> None:
    """Индекс настроения рынка (B1): дневной агрегат сентимента по рынку/секторам/активам.

    Без `--backfill` считает один день (вчера) — как ежедневный джоб. С `--backfill` — ряд за
    `--days` дней (EWMA-перенос по возрастанию). Материализует тренд/breadth/дивергенцию."""
    from geoanalytics.analytics.market_sentiment import backfill as bf
    from geoanalytics.analytics.market_sentiment import latest, record_day
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        rows = bf(session, days=days) if backfill else record_day(session)
        mkt = latest(session, "market")
        if mkt is not None:
            console.print(
                f"[green]Настроение рынка[/] на {mkt.day}: среднее={mkt.sent_mean:+.3f}, "
                f"EWMA={mkt.sent_ewma:+.3f}, breadth={mkt.breadth:+.2f}, "
                f"разброс={mkt.dispersion:.2f} (n={mkt.n_docs})")
    console.print(f"[green]Записано строк:[/] {rows}"
                  + (f" (бэкфилл {days} дн.)" if backfill else " (вчера)"))


@app.command()
def reliability() -> None:
    """Надёжность источников (F7): априор доверия + точность по исходам E2 + доля слухов."""
    from geoanalytics.analytics.source_reliability import source_reliability_report

    reports = source_reliability_report()
    if not reports:
        console.print("[yellow]Нет данных по источникам[/]")
        return
    table = Table(title="Надёжность источников")
    table.add_column("Источник")
    table.add_column("Статей", justify="right")
    table.add_column("Исходов", justify="right")
    table.add_column("Точность", justify="right")
    table.add_column("Априор", justify="right")
    table.add_column("Скор", justify="right")
    table.add_column("Слухи %", justify="right")
    for r in reports:
        acc = f"{r.accuracy:.0%}" if r.accuracy is not None else "—"
        table.add_row(
            r.source, str(r.articles), str(r.n), acc,
            f"{r.prior:.2f}", f"{r.score:.2f}", f"{r.rumor_share:.0%}",
        )
    console.print(table)


@app.command("significance-audit")
def significance_audit(
    hours: int = typer.Option(168, "--hours", "-H", help="Окно анализа распределения."),
) -> None:
    """Б6: аудит каскада значимости — инвариант + распределение + что проходит каждый гейт.

    Заменяет «дрейф порогов руками без A/B» наблюдаемостью: видно, сколько новостей режет
    инжест-фильтр и алерт-гейт, и как они лежат относительно бакетов модели."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from geoanalytics.nlp.significance import (
        significance_bucket,
        significance_gates,
        validate_cascade,
    )
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Article

    gates = significance_gates()
    problems = validate_cascade()
    if problems:
        console.print("[red]Инвариант каскада нарушен:[/]")
        for p in problems:
            console.print(f"  • {p}")
    else:
        console.print(f"[green]Каскад согласован[/]: инжест {gates['ingest']} ≤ "
                      f"алерт {gates['alert']}")

    since = datetime.now(UTC) - timedelta(hours=hours)
    with session_scope() as session:
        vals = [v for (v,) in session.execute(
            select(Article.significance).where(
                Article.published_at >= since, Article.significance.isnot(None))
        )]
    if not vals:
        console.print(f"[yellow]Нет статей за {hours}ч[/]")
        return

    buckets = {"low": 0, "medium": 0, "high": 0}
    for v in vals:
        buckets[significance_bucket(v)] += 1
    n = len(vals)
    ge_alert = sum(1 for v in vals if v >= gates["alert"])
    lt_ingest = sum(1 for v in vals if v < gates["ingest"])
    table = Table(title=f"Распределение значимости за {hours}ч (n={n})")
    table.add_column("Метрика")
    table.add_column("Значение", justify="right")
    table.add_row("медиана", f"{sorted(vals)[n // 2]:.3f}")
    for b in ("low", "medium", "high"):
        table.add_row(f"бакет {b}", f"{buckets[b]} ({buckets[b] / n:.0%})")
    table.add_row(f"≥ алерт-гейт {gates['alert']}", f"{ge_alert} ({ge_alert / n:.0%})")
    table.add_row(f"< инжест {gates['ingest']} (кандидаты на отсев)",
                  f"{lt_ingest} ({lt_ingest / n:.0%})")
    console.print(table)


@app.command("event-study")
def event_study(
    save: bool = typer.Option(False, "--save", help="Сохранить JSON в data/eval/event_study.json."),
    min_n: int = typer.Option(5, "--min-n", help="Минимум исходов на тип события."),
    keep_confounded: bool = typer.Option(
        False, "--keep-confounded", help="Не исключать исходы с соседними событиями другого типа."
    ),
    confound_window: int = typer.Option(
        0, "--confound-window", help="Окно конфаундеров, ±дней (0 = только тот же день)."
    ),
) -> None:
    """Event study (E1): фактическая реакция рынка по типам событий vs ручные веса."""
    import json as _json
    from pathlib import Path

    from geoanalytics.analytics.event_study import event_study_report

    rep = event_study_report(exclude_confounded=not keep_confounded, min_n=min_n,
                             confound_window=confound_window)
    console.print(
        f"Исходов: {rep['total']}, в расчёте: {rep['used']}, "
        f"конфаундеры исключены: {rep['confounded']}"
    )
    table = Table(title="Реакция рынка по типам событий (abnormal return, %)")
    table.add_column("Тип")
    table.add_column("n", justify="right")
    table.add_column("AAR 1д", justify="right")
    table.add_column("|AR| 1д", justify="right")
    table.add_column("|AR| 5д", justify="right")
    table.add_column("hit≥2%", justify="right")
    table.add_column("Вес: факт", justify="right")
    table.add_column("Вес: ручной", justify="right")
    for s in rep["stats"]:
        et = s["event_type"]
        table.add_row(
            et, str(s["n"]),
            f"{s['aar'].get('1', 0):+.2f}", f"{s['mean_abs'].get('1', 0):.2f}",
            f"{s['mean_abs'].get('5', 0):.2f}", f"{s['hit_rate']:.0%}",
            f"{rep['empirical_weights'].get(et, 0):.2f}",
            f"{rep['hand_weights'].get(et, 0):.2f}",
        )
    console.print(table)
    if save:
        out = Path("data/eval/event_study.json")
        out.write_text(_json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[green]Сохранено:[/] {out}")


@app.command()
def attribution(
    ticker: str = typer.Argument(..., help="Тикер актива."),
    day: str | None = typer.Option(None, "--day", "-d",
                                   help="День разложения YYYY-MM-DD (дефолт — последний)."),
    window: int = typer.Option(250, "--window", "-w", help="Окно оценки бет, торговых дней."),
) -> None:
    """Факторная атрибуция (G3): рынок/сектор/FX/Brent → идиосинкразия."""
    from datetime import date as _date

    from geoanalytics.analytics.attribution import attribute_asset
    from geoanalytics.storage.db import session_scope

    target = _date.fromisoformat(day) if day else None
    with session_scope() as session:
        r = attribute_asset(session, ticker, day=target, window=window)
    if r.error:
        console.print(f"[yellow]{r.ticker}: {r.error}[/]")
        return
    console.print(
        f"{r.ticker} {r.day}: доходность [bold]{r.asset_return_pct:+.2f}%[/] "
        f"(R²={r.r2:.2f}, n={r.n_obs})"
    )
    table = Table(title="Разложение дня")
    table.add_column("Фактор")
    table.add_column("β", justify="right")
    table.add_column("Вклад, %", justify="right")
    for name in r.betas:
        table.add_row(name, f"{r.betas[name]:+.3f}",
                      f"{r.contributions_pct[name]:+.2f}")
    table.add_row("α (дневная)", "—", f"{r.alpha_pct:+.2f}")
    table.add_row("[bold]идиосинкразия[/]", "—", f"[bold]{r.idio_pct:+.2f}[/]")
    console.print(table)


@app.command()
def graph(
    ticker: str = typer.Argument(..., help="Тикер актива."),
    hours: int = typer.Option(168, "--hours", "-h", help="Окно событий соседей, часов."),
) -> None:
    """Граф связей эмитента (G7): соседи + косвенные влияния их событий."""
    from sqlalchemy import select

    from geoanalytics.analytics.graph_impact import (
        _neighbors,
        graph_impacts_for_asset,
    )
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset

    with session_scope() as session:
        asset = session.scalars(
            select(Asset).where(Asset.ticker == ticker.upper())).first()
        if asset is None:
            console.print(f"[yellow]Актив {ticker.upper()} не найден[/]")
            return
        neighbors = _neighbors(session, asset.id)
        id_to_ticker = {a.id: a.ticker for a in session.scalars(
            select(Asset).where(Asset.id.in_(list(neighbors) or [0])))}
        impacts = graph_impacts_for_asset(session, asset.id, hours=hours)

    if not neighbors:
        console.print(f"[yellow]У {asset.ticker} нет рёбер графа[/]")
        return
    rel_table = Table(title=f"Связи {asset.ticker}")
    rel_table.add_column("Сосед")
    rel_table.add_column("Связь")
    rel_table.add_column("Вес", justify="right")
    for nid, (_pred, label, weight) in sorted(
            neighbors.items(), key=lambda kv: -kv[1][2]):
        rel_table.add_row(id_to_ticker.get(nid, str(nid)), label, f"{weight:.2f}")
    console.print(rel_table)

    if impacts:
        arrow = {"positive": "[green]▲[/]", "negative": "[red]▼[/]", "neutral": "[dim]■[/]"}
        lines = [f"{arrow.get(g.direction, ' ')} {g.via_ticker} ({g.relation}): "
                 f"{g.title} (сила {g.magnitude})" for g in impacts]
        console.print(Panel("\n".join(lines),
                            title=f"Косвенные влияния за {hours} ч"))
    else:
        console.print("[dim]Значимых событий у соседей за окно нет.[/]")


@app.command()
def regime(
    states: int = typer.Option(3, "--states", "-s", help="Число режимов HMM (2 или 3)."),
    persist: bool = typer.Option(False, "--persist",
                                 help="L5: записать историю режимов в market_regimes."),
) -> None:
    """Режимы рынка (G2): HMM по волатильности IMOEX и USD/RUB."""
    from geoanalytics.analytics.regimes import market_regimes
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        r = market_regimes(session, n_states=states)
        if persist and not r.error:
            from geoanalytics.analytics.regimes import record_regimes
            days = record_regimes(session, n_states=states)
            console.print(f"[green]История режимов записана[/]: дней {days}.")
    if r.error:
        console.print(f"[yellow]{r.error}[/]")
        return
    console.print(
        f"Текущий режим: [bold]{r.current}[/] (с {r.current_since}), "
        f"история {len(r.dates)} торговых дней"
    )
    table = Table(title="Состояния")
    table.add_column("Режим")
    table.add_column("Доля дней", justify="right")
    table.add_column("Ср. vol IMOEX, %/день", justify="right")
    for name in r.labels:
        table.add_row(name, f"{r.state_share[name]:.0%}", f"{r.state_vol[name]:.2f}")
    console.print(table)


@app.command()
def pressure(
    ticker: str = typer.Argument(..., help="Тикер актива."),
    window: int = typer.Option(7, "--window", "-w", help="Окно анализа, дней."),
) -> None:
    """Индекс новостного давления (G5): Σ значимости салиентных новостей / окно."""
    from sqlalchemy import select

    from geoanalytics.analytics.pressure import news_pressure
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset

    with session_scope() as session:
        asset = session.scalars(select(Asset).where(Asset.ticker == ticker.upper())).first()
        if asset is None:
            console.print(f"[red]Актив {ticker.upper()} не найден.[/]")
            raise typer.Exit(code=1)
        value = news_pressure(session, asset.id, window=window)
    console.print(
        f"{ticker.upper()} давление за {window}д: [bold]{value:.4f}[/] "
        f"(Σ sig / {window})"
    )


@app.command("sentiment-trend")
def sentiment_trend(
    ticker: str = typer.Argument(..., help="Тикер актива."),
    days: int = typer.Option(60, "--days", "-d", help="Глубина истории, дней."),
    span: int = typer.Option(14, "--span", help="Период EWMA."),
    last: int = typer.Option(10, "--last", "-n", help="Показать последних N точек."),
) -> None:
    """Тональный моментум (G6): EWMA суточного сентимента по активу."""
    from sqlalchemy import select

    from geoanalytics.analytics.sentiment_trend import sentiment_momentum
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset

    with session_scope() as session:
        asset = session.scalars(select(Asset).where(Asset.ticker == ticker.upper())).first()
        if asset is None:
            console.print(f"[red]Актив {ticker.upper()} не найден.[/]")
            raise typer.Exit(code=1)
        series = sentiment_momentum(session, asset.id, days=days, span=span)
    if not series:
        console.print(f"[yellow]{ticker.upper()}: недостаточно данных сентимента.[/]")
        return
    table = Table(title=f"{ticker.upper()} тональный моментум EWMA-{span}")
    table.add_column("Дата")
    table.add_column("EMA сентимента", justify="right")
    for d, v in series[-last:]:
        color = "green" if v > 0.05 else "red" if v < -0.05 else "white"
        table.add_row(str(d), f"[{color}]{v:+.4f}[/]")
    console.print(table)
    console.print(f"Последнее значение: [bold]{series[-1][1]:+.4f}[/]")


@app.command("alert-outcomes")
def alert_outcomes(
    days: int = typer.Option(30, "--days", "-d", help="Trailing-окно precision, дней."),
    send: bool = typer.Option(False, "--send",
                              help="Отправить отчёт в Telegram (дедуп по неделе)."),
) -> None:
    """Скоринг исходов алертов (E4) + precision по типам — главная метрика системы."""
    from geoanalytics.alerts.outcomes import (
        precision_summary,
        score_alert_outcomes,
        send_weekly_report,
    )

    r = score_alert_outcomes()
    console.print(
        f"[green]Скоринг алертов[/]: новых исходов={r.scored} (hits={r.hits}), "
        f"ждут горизонта={r.pending}, без цен={r.skipped}, ошибки={r.errors}"
    )
    summary = precision_summary(days=days)
    if not summary:
        console.print("Скоренных исходов за окно пока нет.")
    else:
        table = Table(title=f"Precision алертов за {days} дн.")
        table.add_column("Тип")
        table.add_column("Исходов", justify="right")
        table.add_column("Hits", justify="right")
        table.add_column("Precision", justify="right")
        for s in summary:
            table.add_row(s["alert_type"], str(s["n"]), str(s["hits"]),
                          f"{s['precision']:.0%}" if s["precision"] is not None else "—")
        console.print(table)
    if send:
        sent = send_weekly_report(days=days)
        console.print("[green]Отчёт отправлен.[/]" if sent
                      else "Отчёт этой недели уже отправлялся (или нет каналов).")


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


@app.command()
def backfill(
    ticker: str | None = typer.Option(
        None, "--ticker", "-t", help="Тикер; без него — все из справочника."),
    days: int | None = typer.Option(None, "--days", "-d", help="Глубина истории, дней."),
    fx: bool = typer.Option(False, "--fx",
                            help="История курсов ЦБ (USD/EUR/CNY) вместо котировок."),
    metals: bool = typer.Option(False, "--metals",
                                help="История учётных цен металлов ЦБ "
                                     "(золото/серебро/платина/палладий, ₽/г)."),
    brent: bool = typer.Option(False, "--brent",
                               help="История нефти Brent из FRED (DCOILBRENTEU) — "
                                    "активирует brent-фактор атрибуции/whatif."),
) -> None:
    """Загрузить историю дневных котировок с MOEX (для индикаторов)."""
    if brent:
        from geoanalytics.analytics.history import backfill_fred_brent

        br = backfill_fred_brent(days=days)
        if br.error:
            console.print(f"[red]Ошибка: {br.error}[/]")
            raise typer.Exit(1)
        console.print(f"Brent (FRED): загружено точек — {br.points}")
        return
    if metals:
        from geoanalytics.analytics.history import backfill_metals

        mr = backfill_metals(days=days)
        if mr.error:
            console.print(f"[red]Ошибка: {mr.error}[/]")
            raise typer.Exit(1)
        console.print(f"Учётные цены металлов ЦБ: загружено точек — {mr.points}")
        return
    if fx:
        from geoanalytics.analytics.history import backfill_fx

        fx_table = Table(title="Загрузка истории курсов ЦБ")
        fx_table.add_column("Валюта")
        fx_table.add_column("Точек", justify="right")
        fx_table.add_column("Ошибка")
        for fr in backfill_fx(days=days):
            fx_table.add_row(fr.currency, str(fr.points), fr.error or "—")
        console.print(fx_table)
        return
    from geoanalytics.analytics.history import backfill_all, backfill_asset

    results = [backfill_asset(ticker, days)] if ticker else backfill_all(days)
    table = Table(title="Загрузка истории")
    table.add_column("Тикер")
    table.add_column("Свечей", justify="right")
    table.add_column("Ошибка")
    for r in results:
        table.add_row(r.ticker, str(r.candles), r.error or "—")
    console.print(table)


@app.command()
def context(
    ticker: str = typer.Argument(..., help="Тикер для пересборки контекста."),
    no_llm: bool = typer.Option(False, "--no-llm", help="Не вызывать LLM."),
) -> None:
    """Пересобрать и показать контекст актива."""
    from geoanalytics.context.asset_context import build_context

    ctx = build_context(ticker, use_llm=not no_llm)
    if ctx is None:
        console.print(f"[yellow]Актив {ticker.upper()} не найден[/]")
        return
    console.print(Panel(ctx["narrative"], title=f"Контекст {ticker.upper()} (v{ctx['version']})"))


@app.command()
def events(
    hours: int = typer.Option(168, "--hours", "-h", help="Окно в часах."),
    limit: int = typer.Option(20, "--limit", "-n", help="Сколько событий показать."),
) -> None:
    """Последние значимые события и их влияние на активы."""
    from geoanalytics.query.events_feed import recent_events

    table = Table(title=f"События за {hours} ч")
    table.add_column("Тип")
    table.add_column("Заголовок")
    table.add_column("Активы (влияние)")
    mark = {"positive": "▲", "negative": "▼", "neutral": "■"}
    for ev in recent_events(hours=hours, limit=limit):
        assets = ", ".join(
            f"{i['ticker']}{mark.get(i['direction'], '')}{i['magnitude']}"
            for i in ev["impacts"]
        ) or "—"
        table.add_row(ev["event_type"], ev["title"][:60], assets)
    console.print(table)


@app.command()
def alerts(
    no_dispatch: bool = typer.Option(
        False, "--no-dispatch", help="Только вычислить и записать, не отправлять уведомления."
    ),
    hours: int = typer.Option(168, "--hours", "-h", help="Окно ленты для показа, часов."),
    limit: int = typer.Option(20, "--limit", "-n", help="Сколько алертов показать."),
) -> None:
    """Проверить триггеры алертов, разослать новые и показать ленту.

    Триггеры: движение цены ≥ порога, всплеск негатива, новое значимое событие.
    Доставка — в Telegram (если настроен GEO_TELEGRAM_*) и всегда в лог.
    """
    from geoanalytics.alerts.engine import evaluate_and_dispatch
    from geoanalytics.query.alerts_feed import recent_alerts

    res = evaluate_and_dispatch(dispatch=not no_dispatch)
    console.print(
        f"[green]Проверка алертов[/]: сработало={res.evaluated}, новых={res.created}"
    )

    rows = recent_alerts(hours=hours, limit=limit)
    if not rows:
        console.print("[dim]Лента пуста.[/]")
        return
    table = Table(title=f"Алерты за {hours} ч")
    table.add_column("Уровень")
    table.add_column("Тип")
    table.add_column("Тикер")
    table.add_column("Заголовок")
    color = {"info": "dim", "warning": "yellow", "critical": "red"}
    for a in rows:
        sev = a["severity"]
        table.add_row(
            f"[{color.get(sev, 'white')}]{sev}[/]",
            a["alert_type"], a["ticker"] or "—", a["title"][:60],
        )
    console.print(table)


@app.command()
def backtest(
    ticker: str = typer.Argument(..., help="Тикер, напр. SBER"),
    strategy: str = typer.Option(
        "sma_cross", "--strategy", "-S",
        help="Стратегия: sma_cross | momentum | rsi | macd_cross | bollinger "
             "| candles (свечные паттерны Нисона) | sentiment.",
    ),
    fast: int | None = typer.Option(None, "--fast", help="Быстрая SMA (для sma_cross)."),
    slow: int | None = typer.Option(None, "--slow", help="Медленная SMA (для sma_cross)."),
    lookback: int | None = typer.Option(None, "--lookback", help="Окно моментума."),
    cost_bps: float | None = typer.Option(
        None, "--cost-bps", help="Издержка за сторону сделки, б.п. (по умолчанию из настроек)."
    ),
    sentiment_filter: bool = typer.Option(
        False, "--sentiment-filter", "-f",
        help="Тональный фильтр поверх ценового сигнала (лонг только при неотрицательном фоне)."
    ),
) -> None:
    """Бэктест торгового сигнала по истории котировок актива."""
    from geoanalytics.analytics.backtest import backtest_asset

    # Собираем только явно заданные параметры стратегии.
    params = {k: v for k, v in
              {"fast": fast, "slow": slow, "lookback": lookback}.items() if v is not None}
    try:
        res = backtest_asset(ticker, strategy=strategy, params=params, cost_bps=cost_bps,
                             sentiment_filter=sentiment_filter)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    if res is None:
        console.print(f"[yellow]Актив {ticker.upper()} не найден[/]")
        return
    if res.bars < 2:
        console.print(
            f"[yellow]Недостаточно истории. Загрузите: geo backfill -t {ticker.upper()}[/]"
        )
        return

    table = Table(title=f"Бэктест {ticker.upper()} — {strategy}")
    table.add_column("Метрика")
    table.add_column("Значение", justify="right")
    def _fmt(v, suffix="", sign=False):
        if v is None:
            return "—"
        return (f"{v:+.2f}" if sign else f"{v:.2f}") + suffix

    edge = res.total_return_pct - res.buy_hold_return_pct
    edge_str = f"[{'green' if edge >= 0 else 'red'}]{edge:+.2f}%[/]"
    rows = [
        ("Баров", str(res.bars)),
        ("Доходность (чистая)", f"{res.total_return_pct:+.2f}%"),
        ("Доходность (грязная)", f"{res.total_return_gross_pct:+.2f}%"),
        ("Издержки, б.п./сторона", f"{res.cost_bps:g}"),
        ("Buy & Hold", f"{res.buy_hold_return_pct:+.2f}%"),
        ("Преимущество vs B&H", edge_str),
        ("Индекс IMOEX (B&H)", _fmt(res.index_return_pct, "%", sign=True)),
        ("Alpha к индексу", _fmt(res.alpha_pct, "%", sign=True)),
        ("CAGR", _fmt(res.cagr_pct, "%", sign=True)),
        ("Шарп", _fmt(res.sharpe)),
        ("Сортино", _fmt(res.sortino)),
        ("Кальмар", _fmt(res.calmar)),
        ("Макс. просадка", f"-{res.max_drawdown_pct:.2f}%"),
        ("Сделок", str(res.num_trades)),
        ("Доля прибыльных", "—" if res.hit_rate is None else f"{res.hit_rate * 100:.0f}%"),
        ("Profit-factor", _fmt(res.profit_factor)),
        ("Ср. прибыль/убыток", f"{_fmt(res.avg_win_pct, '%', sign=True)} / "
                               f"{_fmt(res.avg_loss_pct, '%', sign=True)}"),
        ("Экспозиция", f"{res.exposure * 100:.0f}%"),
    ]
    for name, value in rows:
        table.add_row(name, value)
    console.print(table)


@app.command()
def walkforward(
    ticker: str = typer.Argument(..., help="Тикер, напр. SBER"),
    strategy: str = typer.Option(
        "sma_cross", "--strategy", "-S",
        help="Стратегия: sma_cross | momentum | rsi | macd_cross | bollinger "
             "| candles (свечные паттерны Нисона).",
    ),
    train: int = typer.Option(120, "--train", help="Длина in-sample окна (баров)."),
    test: int = typer.Option(40, "--test", help="Длина out-of-sample окна (баров)."),
    objective: str = typer.Option(
        "sharpe", "--objective", "-o",
        help="Цель подбора: sharpe | sortino | calmar | cagr | total_return.",
    ),
    anchored: bool = typer.Option(
        False, "--anchored", help="Заякоренный train (растущее окно от начала)."
    ),
    cost_bps: float | None = typer.Option(
        None, "--cost-bps", help="Издержка за сторону сделки, б.п. (по умолчанию из настроек)."
    ),
) -> None:
    """Walk-forward: честная out-of-sample оценка с подбором параметров (анти-overfit)."""
    from geoanalytics.analytics.backtest import walk_forward_asset

    try:
        res = walk_forward_asset(
            ticker, strategy=strategy, train=train, test=test,
            objective=objective, anchored=anchored, cost_bps=cost_bps,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    if res is None:
        console.print(f"[yellow]Актив {ticker.upper()} не найден[/]")
        return
    if not res.folds:
        console.print(
            f"[yellow]Недостаточно истории для окон train={train}+test={test}. "
            f"Уменьшите окна или загрузите: geo backfill -t {ticker.upper()}[/]"
        )
        return

    eff_str = "—"
    if res.efficiency is not None:
        color = "green" if res.efficiency >= 0.5 else "red"
        eff_str = f"[{color}]{res.efficiency:.2f}[/]"

    summary = Table(title=f"Walk-forward {ticker.upper()} — {strategy} (цель: {objective})")
    summary.add_column("Метрика")
    summary.add_column("Значение", justify="right")
    for name, value in [
        ("Фолдов", str(len(res.folds))),
        (f"Окна (train/test, {'якорь' if res.anchored else 'скольж.'})",
         f"{res.train}/{res.test}"),
        ("OOS доходность (честная)", f"{res.oos_return_pct:+.2f}%"),
        ("OOS Buy & Hold", f"{res.oos_buy_hold_pct:+.2f}%"),
        ("OOS Индекс IMOEX", "—" if res.oos_index_pct is None
         else f"{res.oos_index_pct:+.2f}%"),
        ("OOS Alpha к индексу", "—" if res.oos_alpha_pct is None
         else f"{res.oos_alpha_pct:+.2f}%"),
        ("OOS Шарп", "—" if res.oos_sharpe is None else f"{res.oos_sharpe:.2f}"),
        ("OOS макс. просадка", f"-{res.oos_max_drawdown_pct:.2f}%"),
        ("IS доходность (оптимизм)", f"{res.is_return_pct:+.2f}%"),
        ("Walk-forward efficiency", eff_str),
    ]:
        summary.add_row(name, value)
    console.print(summary)

    folds = Table(title="Фолды (подобранные параметры и IS→OOS доходность)")
    folds.add_column("#", justify="right")
    folds.add_column("Train")
    folds.add_column("Test")
    folds.add_column("Параметры")
    folds.add_column("IS %", justify="right")
    folds.add_column("OOS %", justify="right")
    for i, f in enumerate(res.folds, 1):
        params = ", ".join(f"{k}={v}" for k, v in sorted(f.best_params.items())) or "—"
        oos_color = "green" if f.test_return_pct >= 0 else "red"
        folds.add_row(
            str(i), f"{f.train_start}:{f.train_end}", f"{f.test_start}:{f.test_end}",
            params, f"{f.train_return_pct:+.2f}",
            f"[{oos_color}]{f.test_return_pct:+.2f}[/]",
        )
    console.print(folds)
    console.print(
        "[dim]efficiency = прирост OOS / прирост IS; <0.5 — признак переобучения, "
        "<0 — стратегия теряет вне выборки.[/]"
    )


def _rich_link(text: str, url: str | None) -> str:
    """Кликабельная ссылка для rich-панели; без url — простой текст."""
    return f"[link={url}]{text}[/link]" if url else text


@app.command()
def asset(
    ticker: str = typer.Argument(..., help="Тикер, напр. SBER"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Не вызывать LLM."),
    no_rebuild: bool = typer.Option(False, "--no-rebuild", help="Не пересобирать контекст."),
    period: str = typer.Option(
        "D", "--period", "-p",
        help="Таймфрейм индикаторов: D (дни) | W (недели) | M (месяцы)."
    ),
) -> None:
    """Аналитика по конкретному активу: индикаторы, макро, факторы, новости, контекст."""
    from geoanalytics.query.asset_report import build_report

    rep = build_report(ticker, rebuild=not no_rebuild, use_llm=not no_llm,
                       period=period.upper())
    if not rep.found:
        console.print(Panel(rep.note, title=f"{rep.ticker}", style="yellow"))
        return

    header = f"{rep.name} ({rep.ticker})" + (f" — {rep.sector}" if rep.sector else "")
    if rep.narrative:
        console.print(Panel(rep.narrative, title=header))
    else:
        console.print(Panel(header, title="Актив"))

    # Индикаторы.
    ind = rep.indicators
    if ind:
        _tf = {"D": "дни", "W": "недели", "M": "месяцы"}.get(period.upper(), period)
        table = Table(title=f"Технические индикаторы ({_tf})")
        table.add_column("Показатель")
        table.add_column("Значение", justify="right")
        labels = {
            "last": "Цена", "trend": "Тренд", "rsi14": "RSI(14)",
            "stoch_k": "Стох. %K", "stoch_d": "Стох. %D", "atr14": "ATR(14)",
            "macd": "MACD", "macd_signal": "MACD сигнал", "macd_hist": "MACD гист.",
            "boll_lower": "Bollinger ↓", "boll_mid": "Bollinger сред.", "boll_upper": "Bollinger ↑",
            "sma50": "SMA50", "sma200": "SMA200", "vol_annual": "Волат., %год",
            "obv": "OBV", "vol_sma20": "Ср. объём 20", "vol_ratio": "Объём ×ср.",
            "ret_1w": "Доход. 1н, %", "ret_1m": "Доход. 1м, %", "ret_3m": "Доход. 3м, %",
            "high_52w": "Макс 52н", "low_52w": "Мин 52н",
            "pct_from_52w_high": "% от макс 52н", "pct_from_52w_low": "% от мин 52н",
        }
        for key, label in labels.items():
            if key in ind:
                table.add_row(label, str(ind[key]))
        console.print(table)
    else:
        console.print("[yellow]Нет истории котировок. Загрузите: "
                      f"geo backfill -t {rep.ticker}[/]")

    # Макро и факторы.
    macro_lines = []
    if rep.macro.get("key_rate") is not None:
        macro_lines.append(f"Ключевая ставка: {rep.macro['key_rate']}%")
    for cur, val in (rep.macro.get("fx") or {}).items():
        macro_lines.append(f"{cur}/RUB: {val:.2f}")
    if rep.factors.get("macro_factors"):
        macro_lines.append("Драйверы: " + ", ".join(rep.factors["macro_factors"]))
    if rep.factors.get("peers"):
        macro_lines.append("Пиры: " + ", ".join(rep.factors["peers"]))
    if macro_lines:
        console.print(Panel("\n".join(macro_lines), title="Макро и факторы"))

    # Корреляции актив ↔ факторы.
    if rep.correlations:
        names = {"usd_rub": "USD/RUB", "usd_eur": "USD/EUR", "brent": "Нефть Brent",
                 "gold": "Золото ₽/г", "silver": "Серебро ₽/г",
                 "platinum": "Платина ₽/г", "palladium": "Палладий ₽/г",
                 "sector_peers": "Сектор-пиры"}
        lines = [f"{names.get(k, k)}: [bold]{v:+.2f}[/]" for k, v in rep.correlations.items()]
        console.print(Panel("\n".join(lines), title="Корреляции (дн. доходности)"))

    # F5: дивиденд из новостей + доходность к текущей цене.
    if rep.dividend:
        d = rep.dividend
        when = d["published_at"].strftime("%d.%m.%Y") if d.get("published_at") else "?"
        line = f"Дивиденд (из новостей {when}): {d['value']:g} ₽/акция"
        if d.get("yield_pct") is not None:
            line += f" ≈ {d['yield_pct']}% доходности к текущей цене"
        console.print(Panel(line, title="Дивиденды"))

    # G5/G6: новостное давление и тональный моментум.
    news_lines = []
    if rep.news_pressure_7d is not None:
        news_lines.append(f"Давление 7д: [bold]{rep.news_pressure_7d:.4f}[/] (Σ sig / 7)")
    if rep.sentiment_ema_14d is not None:
        color = ("green" if rep.sentiment_ema_14d > 0.05
                 else "red" if rep.sentiment_ema_14d < -0.05 else "white")
        news_lines.append(f"Тональный моментум EWMA-14: [{color}]{rep.sentiment_ema_14d:+.4f}[/]")
    if news_lines:
        console.print(Panel("\n".join(news_lines), title="Новостной фон"))

    # Топ-события влияния.
    if rep.events:
        arrow = {"positive": "[green]▲[/]", "negative": "[red]▼[/]", "neutral": "[dim]■[/]"}
        lines = [f"{arrow.get(e['direction'], ' ')} [{e['type']}] "
                 f"{_rich_link(e['title'], e.get('url'))} (сила {e['magnitude']})"
                 for e in rep.events]
        console.print(Panel("\n".join(lines), title="События влияния"))

    # G7: косвенные влияния через граф связей (события соседей-эмитентов).
    if rep.graph_impacts:
        arrow = {"positive": "[green]▲[/]", "negative": "[red]▼[/]", "neutral": "[dim]■[/]"}
        lines = [f"{arrow.get(g['direction'], ' ')} {g['via']} ({g['relation']}): "
                 f"{_rich_link(g['title'], g.get('url'))} (сила {g['magnitude']})"
                 for g in rep.graph_impacts]
        console.print(Panel("\n".join(lines), title="Косвенно через граф связей"))

    # Новости.
    if rep.news:
        marker = {"positive": "[green]▲[/]", "negative": "[red]▼[/]", "neutral": "[dim]■[/]"}
        lines = [f"{marker.get(n.get('sentiment'), ' ')} {_rich_link(n['title'], n.get('url'))}"
                 for n in rep.news[:10]]
        console.print(Panel("\n".join(lines), title="Связанные новости"))

    if rep.note:
        console.print(f"[yellow]{rep.note}[/]")


portfolio_app = typer.Typer(
    help="Виртуальный портфель (J1): позиции, риск, экспозиция.",
    invoke_without_command=True,
)
app.add_typer(portfolio_app, name="portfolio")


fundamentals_app = typer.Typer(help="Фундаменталка эмитентов из отчётов (H5).")
app.add_typer(fundamentals_app, name="fundamentals")


@fundamentals_app.command("add")
def fundamentals_add(
    ticker: str = typer.Argument(..., help="Тикер актива (SBER)."),
    path: str = typer.Argument(..., help="PDF-отчёт или .txt с текстом отчёта."),
    period: str = typer.Option(None, "--period", "-p",
                               help="Период (2024 / 2024-H1); без него — автодетект."),
    source: str = typer.Option("pdf", "--source", help="Метка источника."),
) -> None:
    """Разобрать отчёт эмитента (PDF/текст) → фундаментальные метрики в карточку актива.

    `geo fundamentals add SBER report.pdf` — извлечёт выручку/прибыль/EBITDA/… (rule-based,
    precision-first) и сохранит. Идемпотентно (повторный разбор того же периода перезапишет)."""
    from geoanalytics.analytics.fundamentals import (
        ingest_fundamentals,
        read_source_text,
    )
    from geoanalytics.storage.db import session_scope

    try:
        text = read_source_text(path)
    except (FileNotFoundError, RuntimeError) as exc:
        console.print(f"[red]Ошибка чтения[/]: {exc}")
        raise typer.Exit(1) from exc
    with session_scope() as session:
        res = ingest_fundamentals(session, ticker, text, period=period, source=source)
    if not res.found:
        console.print(f"[yellow]{res.note}[/]")
        raise typer.Exit(1)
    if not res.stored:
        console.print("[yellow]Метрик не извлечено (нет узнаваемых меток/единиц в тексте).[/]")
        return
    console.print(f"[green]✓[/] {res.ticker}: извлечено метрик {res.stored}")
    fundamentals_list(res.ticker)


@fundamentals_app.command("scrape")
def fundamentals_scrape(
    ticker: str = typer.Argument(None, help="Тикер (SBER); без него — нужен --all."),
    all_shares: bool = typer.Option(False, "--all", help="Все акции из справочника."),
    delay: float = typer.Option(1.0, "--delay", help="Пауза между запросами, сек (вежливость)."),
) -> None:
    """Скрейп годовой отчётности МСФО с smart-lab.ru → метрики по годам в карточку актива.

    `geo fundamentals scrape SBER` — один тикер; `geo fundamentals scrape --all` — все акции.
    Многопериодно и идемпотентно (source=smartlab). Источник хрупкий: при сбое тикер
    пропускается без падения."""
    import time

    from sqlalchemy import select

    from geoanalytics.analytics.fundamentals import scrape_fundamentals
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset

    if not ticker and not all_shares:
        console.print("[yellow]Укажите тикер или --all.[/]")
        raise typer.Exit(1)
    with session_scope() as session:
        if all_shares:
            tickers = list(session.scalars(
                select(Asset.ticker).where(Asset.kind == "share").order_by(Asset.ticker)))
        else:
            tickers = [ticker.upper()]
        table = Table(title="Скрейп фундаменталки (smart-lab)")
        table.add_column("Тикер")
        table.add_column("Метрик", justify="right")
        table.add_column("Статус")
        total = 0
        for i, tk in enumerate(tickers):
            res = scrape_fundamentals(session, tk)
            total += res.stored
            status = "[green]ок[/]" if res.stored else f"[yellow]{res.note or 'пусто'}[/]"
            table.add_row(tk, str(res.stored), status)
            if delay and i < len(tickers) - 1:
                time.sleep(delay)
    console.print(table)
    console.print(f"Всего метрик сохранено: [bold]{total}[/] по {len(tickers)} тикерам.")


@fundamentals_app.command("list")
def fundamentals_list(
    ticker: str = typer.Argument(..., help="Тикер актива."),
) -> None:
    """Показать сохранённые фундаментальные метрики актива (свежие по каждой)."""
    from sqlalchemy import select

    from geoanalytics.analytics.fundamentals import fundamentals_for_asset
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset

    with session_scope() as session:
        asset = session.scalars(
            select(Asset).where(Asset.ticker == ticker.upper())).first()
        if asset is None:
            console.print(f"[yellow]Актив {ticker.upper()} не найден.[/]")
            raise typer.Exit(1)
        rows = fundamentals_for_asset(session, asset.id)
    if not rows:
        console.print(f"[yellow]Нет фундаментальных метрик для {ticker.upper()}. "
                      f"Загрузите: geo fundamentals add {ticker.upper()} отчёт.pdf[/]")
        return
    table = Table(title=f"Фундаменталка — {ticker.upper()}")
    table.add_column("Метрика")
    table.add_column("Значение", justify="right")
    table.add_column("Период")
    for r in rows:
        table.add_row(r["label"], r["display"], r["period"] or "—")
    console.print(table)


segments_app = typer.Typer(help="Сегменты выручки эмитента (L2: состав компании).")
app.add_typer(segments_app, name="segments")


@segments_app.command("add")
def segments_add(
    ticker: str = typer.Argument(..., help="Тикер актива (SBER)."),
    segment: str = typer.Option(..., "--segment", "-s", help="Название сегмента."),
    value: float = typer.Option(..., "--value", "-v", help="Выручка сегмента, ₽."),
    share: float = typer.Option(None, "--share", help="Доля сегмента в выручке, %."),
    period: str = typer.Option(None, "--period", "-p", help="Период (2024)."),
    source: str = typer.Option("manual", "--source", help="Метка источника."),
) -> None:
    """Добавить/обновить сегмент выручки эмитента (идемпотентно по компании/сегменту/периоду).

    `geo segments add MTSS --segment "Связь" --value 500000000000 --share 70 --period 2024`."""
    from sqlalchemy import select

    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset
    from geoanalytics.storage.repositories import RevenueSegmentRepository

    with session_scope() as session:
        asset = session.scalars(select(Asset).where(Asset.ticker == ticker.upper())).first()
        if asset is None:
            console.print(f"[yellow]Актив {ticker.upper()} не найден.[/]")
            raise typer.Exit(1)
        if asset.company_id is None:
            console.print(f"[yellow]У актива {ticker.upper()} нет привязанной компании.[/]")
            raise typer.Exit(1)
        RevenueSegmentRepository(session).upsert(
            asset.company_id, segment, value, share=share, period=period, source=source)
    console.print(f"[green]✓[/] {ticker.upper()}: сегмент «{segment}» сохранён")
    segments_list(ticker)


@segments_app.command("list")
def segments_list(
    ticker: str = typer.Argument(..., help="Тикер актива."),
) -> None:
    """Показать сегменты выручки эмитента за свежайший период."""
    from sqlalchemy import select

    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset
    from geoanalytics.storage.repositories import RevenueSegmentRepository

    with session_scope() as session:
        asset = session.scalars(select(Asset).where(Asset.ticker == ticker.upper())).first()
        if asset is None or asset.company_id is None:
            console.print(f"[yellow]Актив {ticker.upper()} не найден или без компании.[/]")
            raise typer.Exit(1)
        rows = RevenueSegmentRepository(session).for_company(asset.company_id)
        data = [(r.segment, r.value, r.share, r.period) for r in rows]
    if not data:
        console.print(f"[yellow]Нет сегментов для {ticker.upper()}. "
                      f"Добавьте: geo segments add {ticker.upper()} --segment … --value …[/]")
        return
    table = Table(title=f"Сегменты выручки — {ticker.upper()}")
    table.add_column("Сегмент")
    table.add_column("Выручка, ₽", justify="right")
    table.add_column("Доля", justify="right")
    table.add_column("Период")
    for seg, val, sh, per in data:
        table.add_row(seg, f"{val:,.0f}".replace(",", " "),
                      f"{sh:.0f}%" if sh is not None else "—", per or "—")
    console.print(table)


futures_intraday_app = typer.Typer(
    help="Интрадей-данные и симулятор фьючерсов FORTS (Трек 2 / T2.1–T2.2).")
app.add_typer(futures_intraday_app, name="futures-intraday")

futures_depth_app = typer.Typer(
    help="Захват стакана (L2 depth) фьючерсов FORTS (Трек 2, миграция 0037).")
app.add_typer(futures_depth_app, name="futures-depth")


@futures_depth_app.command("capture")
def futures_depth_capture(
    interval_sec: float = typer.Option(5.0, "--interval-sec", help="Период снятия, секунд."),
    once: bool = typer.Option(False, "--once", help="Один снимок и выход (для проверки)."),
    all_hours: bool = typer.Option(False, "--all-hours", help="Снимать вне торговой сессии тоже."),
) -> None:
    """Снять микроструктуру стакана (best/спред/дисбаланс) фронт-контрактов в futures_orderbook.
    Без --once — бесконечный цикл (служба geo-depth): история копится ТОЛЬКО ВПЕРЁД (ISS не отдаёт
    прошлые снимки; полный L2-ладдер анонимно недоступен — берём агрегатную глубину marketdata)."""
    from geoanalytics.futrader.depth import capture_loop, capture_once
    from geoanalytics.storage.db import session_scope

    if once:
        with session_scope() as session:
            n = capture_once(session)
        console.print(f"[green]✓[/] снимков микроструктуры записано: {n}")
        return
    console.print(f"[cyan]Захват микроструктуры каждые {interval_sec}s (Ctrl-C для выхода)…[/]")
    capture_loop(interval_sec=interval_sec, only_in_session=not all_hours)


@futures_depth_app.command("status")
def futures_depth_status() -> None:
    """Сколько снимков стакана накоплено и свежесть по инструментам."""
    from geoanalytics.futrader.accumulate import DEFAULT_TICKERS
    from geoanalytics.futrader.data import _asset_code_for
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import FuturesOrderbookRepository

    with session_scope() as session:
        repo = FuturesOrderbookRepository(session)
        total = repo.count()
        console.print(f"Снимков стакана всего: [bold]{total}[/]")
        for tk in DEFAULT_TICKERS:
            row = repo.latest(_asset_code_for(tk))
            if row is not None:
                console.print(f"  {tk:>4}: последний {row.ts:%Y-%m-%d %H:%M} "
                              f"bid={row.best_bid} ask={row.best_ask} imb={row.imbalance}")


@futures_intraday_app.command("backfill")
def futures_intraday_backfill(
    asset: str = typer.Option(..., "--asset", "-a", help="Тикер фьючерса (BR/GD/SI/EU/CNY/RTS)."),
    interval: str = typer.Option("1m", "--interval", "-i", help="1m | 10m | 1h."),
    days: int = typer.Option(7, "--days", "-d", help="Глубина окна, дней."),
    max_contracts: int = typer.Option(3, "--max-contracts", help="Сколько ближайших контрактов."),
) -> None:
    """Загрузить интрадей-свечи фьючерса по контрактам в futures_candles.

    Минутки ISS отдаёт лишь за недавнее окно — берём что есть. Идемпотентно (повтор не плодит)."""
    from geoanalytics.futrader.data import backfill_futures_intraday
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        try:
            added = backfill_futures_intraday(session, asset, interval=interval, days=days,
                                              max_contracts=max_contracts)
        except ValueError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from exc
    console.print(f"[green]✓[/] {asset.upper()} {interval}: новых свечей {added}")


@futures_intraday_app.command("continuous")
def futures_intraday_continuous(
    asset: str = typer.Option(..., "--asset", "-a", help="Тикер фьючерса."),
    interval: str = typer.Option("1h", "--interval", "-i", help="1m | 10m | 1h."),
) -> None:
    """Построить непрерывный (склееный) контракт из futures_candles и показать стыки-роллы."""
    from geoanalytics.futrader.continuous import continuous_series
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        series = continuous_series(session, asset, interval=interval)
    if not series.bars:
        console.print(f"[yellow]Нет данных для {asset.upper()} {interval}. Сначала: "
                      f"geo futures-intraday backfill -a {asset.upper()} -i {interval}[/]")
        return
    b0, b1 = series.bars[0], series.bars[-1]
    console.print(
        f"Непрерывный контракт [bold]{asset.upper()}[/] {interval}: баров {len(series.bars)}, "
        f"с {b0.ts:%Y-%m-%d %H:%M} ({b0.close:.2f}) по {b1.ts:%Y-%m-%d %H:%M} ({b1.close:.2f})")
    if series.rolls:
        table = Table(title="Роллы (стыки контрактов)")
        table.add_column("Дата")
        table.add_column("С")
        table.add_column("На")
        table.add_column("Коэф.", justify="right")
        for r in series.rolls:
            table.add_row(f"{r['ts']:%Y-%m-%d}", r["from_secid"], r["to_secid"],
                          f"{r['factor']:.4f}")
        console.print(table)
    else:
        console.print("[dim]Один контракт — роллов нет.[/]")


@futures_intraday_app.command("accumulate")
def futures_intraday_accumulate(
    interval: str = typer.Option(None, "--interval", "-i",
                                 help="Один интервал (по умолчанию час+день)."),
    days: int = typer.Option(None, "--days", "-d",
                             help="Окно бэкфилла, дней (по умолч. — по интервалу)."),
    horizon: int = typer.Option(12, "--horizon", "-H", help="Горизонт разметки исхода, баров."),
) -> None:
    """Фаза 0: накопить пулинг-датасет — бэкфилл ВСЕХ фьючерсов × интервалов + лог ВСЕХ стратегий.

    Узкое место Трека 2 — глубина данных. Команда копит её разом по всем инструментам в общий
    размеченный датасет futures_decisions (пулинг для T2.4). Идемпотентно; то же гоняет ежедневный
    джоб scheduler (офф-пик). Тяжёлый проход по ISS — запускайте вне пиковой нагрузки."""
    from geoanalytics.futrader.accumulate import DEFAULT_INTERVALS, accumulate_dataset
    from geoanalytics.storage.db import session_scope

    intervals = (interval,) if interval else DEFAULT_INTERVALS
    with session_scope() as session:
        res = accumulate_dataset(session, intervals=intervals, days=days, horizon_bars=horizon)
    table = Table(title="Накопление пулинг-датасета (Трек 2 / Фаза 0)")
    for col in ("Инструмент", "Интервал", "Свечей+", "Решений", "Размечено"):
        table.add_column(col)
    for s in res.stats:
        table.add_row(s.ticker, s.interval, f"{s.candles}", f"{s.decisions}", f"{s.labeled}")
    console.print(table)
    console.print(f"[green]✓[/] Итого: свечей +{res.candles}, решений {res.decisions}, "
                  f"размечено {res.labeled}")


@futures_intraday_app.command("simulate")
def futures_intraday_simulate(
    asset: str = typer.Option(..., "--asset", "-a", help="Тикер фьючерса (BR/GD/SI/EU/CNY/RTS)."),
    interval: str = typer.Option("1h", "--interval", "-i", help="1m | 10m | 1h."),
    cash: float = typer.Option(100_000.0, "--cash", help="Стартовый капитал, ₽."),
    contracts: int = typer.Option(1, "--contracts", "-n", help="Контрактов в buy-and-hold."),
) -> None:
    """T2.2: прогнать симулятор исполнения по непрерывному ряду (демо buy-and-hold лонг).

    Проверка движка end-to-end на живых данных: спека контракта берётся с ISS, P&L считается в ₽
    через стоимость шага цены, учтены ГО/комиссия/проскальзывание. Стратегия — открыть лонг на
    первом баре и держать; реальные политики придут в T2.3/T2.4."""
    from geoanalytics.analytics.history import _front_futures_secid
    from geoanalytics.futrader.continuous import continuous_series
    from geoanalytics.futrader.data import _asset_code_for, fetch_contract_spec
    from geoanalytics.futrader.execution import ExecutionSimulator, Order
    from geoanalytics.storage.db import session_scope

    secid = _front_futures_secid(_asset_code_for(asset))
    if secid is None:
        console.print(f"[red]Нет активного контракта для {asset.upper()}.[/]")
        raise typer.Exit(1)
    spec = fetch_contract_spec(secid)
    if spec is None:
        console.print(f"[red]Не удалось получить спецификацию контракта {secid}.[/]")
        raise typer.Exit(1)
    with session_scope() as session:
        series = continuous_series(session, asset, interval=interval)
    if not series.bars:
        console.print(f"[yellow]Нет данных. Сначала: geo futures-intraday backfill "
                      f"-a {asset.upper()} -i {interval}[/]")
        return

    sim = ExecutionSimulator(spec, starting_cash=cash)
    opened = {"done": False}

    def buy_and_hold(_bar, _sim):
        if opened["done"]:
            return None
        opened["done"] = True
        return Order(side="buy", qty=contracts)

    res = sim.run(series.bars, strategy=buy_and_hold)
    console.print(
        f"[bold]{asset.upper()}[/] {interval} · контракт {secid} (ГО {spec.initial_margin:,.0f}₽, "
        f"шаг {spec.tick_size:g}→{spec.tick_value:g}₽, комиссия {spec.fee:g}₽)")
    console.print(
        f"Баров {len(series.bars)} · сделок {res.n_trades} (отклонено {res.rejected}) · "
        f"комиссия {res.fees_paid:,.0f}₽")
    color = "green" if res.return_pct >= 0 else "red"
    console.print(
        f"Эквити {res.starting_cash:,.0f}₽ → [bold {color}]{res.final_equity:,.0f}₽[/] "
        f"([{color}]{res.return_pct:+.2f}%[/]) · просадка {res.max_drawdown_rub:,.0f}₽"
        + (" · [red]ЛИКВИДАЦИЯ[/]" if res.liquidated else ""))


@futures_intraday_app.command("log-decisions")
def futures_intraday_log_decisions(
    asset: str = typer.Option(..., "--asset", "-a", help="Тикер фьючерса (BR/GD/SI/EU/CNY/RTS)."),
    interval: str = typer.Option("1h", "--interval", "-i", help="1m | 10m | 1h."),
    source: str = typer.Option("sma_cross", "--strategy", "-s",
                               help="Политика: sma_cross|momentum|rsi|macd|bollinger."),
    horizon: int = typer.Option(12, "--horizon", "-H", help="Горизонт разметки исхода, баров."),
    qty: int = typer.Option(1, "--qty", "-q", help="Контрактов на решение."),
) -> None:
    """T2.3: прогнать политику по непрерывному ряду → лог решений + признаки + исходы.

    Накопительная обучающая выборка для T2.4 (fine-tune на своих исходах). Идемпотентно: повтор
    обновляет разметку по дозревшим данным."""
    from geoanalytics.futrader.decisions import log_decisions
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        try:
            res = log_decisions(session, asset, interval=interval, source=source,
                                qty=qty, horizon_bars=horizon)
        except ValueError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from exc
    if not res.decisions:
        console.print(f"[yellow]Нет данных. Сначала: geo futures-intraday backfill "
                      f"-a {asset.upper()} -i {interval}[/]")
        return
    wr = f"{res.win_rate:.0%}" if res.win_rate is not None else "—"
    console.print(
        f"[green]✓[/] {asset.upper()} {interval} · политика {source}: решений {len(res.decisions)} "
        f"(записано {res.stored}), размечено {res.labeled}, win-rate {wr} "
        f"(горизонт {horizon} баров)")


@futures_intraday_app.command("decisions")
def futures_intraday_decisions(
    asset: str = typer.Option(..., "--asset", "-a", help="Тикер фьючерса."),
    interval: str = typer.Option("1h", "--interval", "-i", help="1m | 10m | 1h."),
    source: str = typer.Option(None, "--strategy", "-s", help="Фильтр по политике (опц.)."),
    limit: int = typer.Option(20, "--limit", "-n", help="Сколько последних показать."),
) -> None:
    """Показать последние залогированные решения с признаками и исходом."""
    from geoanalytics.futrader.data import _asset_code_for
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import FuturesDecisionRepository

    code = _asset_code_for(asset)
    with session_scope() as session:
        rows = FuturesDecisionRepository(session).recent(code, interval, source=source,
                                                         limit=limit)
        rows = [(r.ts, r.source, r.action, r.signed_qty, r.price, r.label,
                 r.outcome_return_pct, r.outcome_pnl_rub) for r in rows]
    if not rows:
        console.print(f"[yellow]Нет решений для {asset.upper()} {interval}. Сначала: "
                      f"geo futures-intraday log-decisions -a {asset.upper()} -i {interval}[/]")
        return
    table = Table(title=f"Решения {asset.upper()} {interval}")
    for col in ("Время", "Политика", "Действие", "Qty", "Цена", "Исход", "Δ%", "P&L ₽"):
        table.add_column(col)
    for ts, src, action, sq, price, label, ret, pnl in rows:
        lab = {"win": "[green]win[/]", "loss": "[red]loss[/]", "flat": "[dim]flat[/]"}.get(
            label, "[dim]—[/]")
        table.add_row(f"{ts:%m-%d %H:%M}", src, action, f"{sq:+d}", f"{price:.2f}", lab,
                      f"{ret:+.2f}" if ret is not None else "—",
                      f"{pnl:+,.0f}" if pnl is not None else "—")
    console.print(table)


@futures_intraday_app.command("train-policy")
def futures_intraday_train_policy(
    asset: str = typer.Option(None, "--asset", "-a",
                              help="Тикер (опц.; без него — учить на всех активах политики)."),
    interval: str = typer.Option("1h", "--interval", "-i", help="1m | 10m | 1h (для бэктеста)."),
    source: str = typer.Option("sma_cross", "--strategy", "-s",
                               help="Политика: sma_cross|momentum|rsi|macd|bollinger."),
    threshold: float = typer.Option(0.55, "--threshold", "-t", help="Порог P(win) для сделки."),
    min_samples: int = typer.Option(30, "--min-samples", help="Минимум размеченных решений."),
    backtest: bool = typer.Option(True, "--backtest/--no-backtest",
                                  help="Сравнить raw-правило vs фильтр в симуляторе."),
) -> None:
    """T2.4: обучить мета-фильтр P(win) на размеченных решениях + честные метрики (+ бэктест).

    Правило предлагает сделку, модель гейтит/сайзит по P(win). Оценка — time-ordered hold-out
    (финданные не перемешиваем). Накопить решения: geo futures-intraday log-decisions."""
    from geoanalytics.futrader.data import _asset_code_for
    from geoanalytics.futrader.policy import (
        evaluate_on_simulator,
        load_policy,
        train_policy,
    )
    from geoanalytics.storage.db import session_scope

    code = _asset_code_for(asset) if asset else None
    with session_scope() as session:
        res = train_policy(session, source=source, asset_code=code,
                           threshold=threshold, min_samples=min_samples)
        if not res.trained:
            console.print(f"[yellow]Не обучено:[/] {res.note} "
                          f"(всего размечено {res.n_total})")
            return
        console.print(
            f"[green]✓[/] Политика {source} ({code or 'all'}): обучено на {res.n_train}, "
            f"тест {res.n_test}. Модель → {res.model_path}")
        base = f"{res.base_win_rate:.0%}" if res.base_win_rate is not None else "—"
        prec = f"{res.model_precision:.0%}" if res.model_precision is not None else "—"
        lift = f"{res.lift:+.1%}" if res.lift is not None else "—"
        auc = f"{res.auc:.3f}" if res.auc is not None else "—"
        console.print(
            f"  Hold-out: база win-rate {base} → модель {prec} (взято {res.n_taken} сделок, "
            f"lift {lift}); AUC {auc}")
        if res.lift is not None and res.lift <= 0:
            console.print("  [yellow]Фильтр пока НЕ улучшает базу — нужно больше данных/"
                          "дозревания (это ожидаемо на крошечной выборке).[/]")

        if backtest and asset:
            policy = load_policy(code, source)
            out = evaluate_on_simulator(session, policy, asset, interval,
                                        source=source, threshold=threshold) if policy else None
            if out:
                raw, gated = out
                console.print(
                    f"  Бэктест хвоста {asset.upper()} {interval}: "
                    f"raw {raw.return_pct:+.2f}% (сделок {raw.n_trades}) | "
                    f"фильтр {gated.return_pct:+.2f}% (сделок {gated.n_trades})")


def _fmt(v, spec=".3f", pct=False):
    if v is None:
        return "—"
    return f"{v:+.1%}" if pct else f"{v:{spec}}"


@futures_intraday_app.command("evaluate")
def futures_intraday_evaluate(
    asset: str = typer.Option(None, "--asset", "-a", help="Тикер (опц.; без него — пулинг)."),
    interval: str = typer.Option("1h", "--interval", "-i", help="1m | 10m | 1h | 1d."),
    strategy: str = typer.Option(None, "--strategy", "-s", help="Одна политика (по умолч. все)."),
    threshold: float = typer.Option(0.55, "--threshold", "-t", help="Порог P(win) гейта."),
    splits: int = typer.Option(5, "--splits", help="Число walk-forward фолдов."),
    record: bool = typer.Option(True, "--record/--no-record",
                                help="Писать прогон в реестр + обновлять чемпиона."),
) -> None:
    """Фаза B: walk-forward оценка политики (Sharpe/Sortino/maxDD/deflated Sharpe) + реестр моделей.

    Несколько последовательных OOS-окон с эмбарго (против утечки времени). deflated Sharpe штрафует
    за мультитестинг (число прогоняемых политик). Запись в `futures_model_runs`, авто-выбор чемпиона
    (только при положительном lift и улучшении DSR — консервативно к шуму)."""
    from geoanalytics.futrader.data import _asset_code_for
    from geoanalytics.futrader.decisions import SIGNAL_FNS
    from geoanalytics.futrader.evaluation import evaluate_and_record, run_walk_forward
    from geoanalytics.futrader.signals import CROSS_SECTIONAL
    from geoanalytics.storage.db import session_scope

    code = _asset_code_for(asset) if asset else None
    strategies = [strategy] if strategy else list(SIGNAL_FNS) + list(CROSS_SECTIONAL)
    n_trials = len(strategies)
    table = Table(title=f"Walk-forward оценка ({code or 'пулинг'} {interval})")
    for col in ("Политика", "Решений", "Фолд", "Взято", "База", "Модель", "Lift",
                "AUC", "Sharpe", "maxDD", "PF", "DSR", "Brier", "CalGap"):
        table.add_column(col)
    with session_scope() as session:
        for s in strategies:
            runner = evaluate_and_record if record else run_walk_forward
            res = runner(session, source=s, asset_code=code, interval=interval,
                         threshold=threshold, n_splits=splits, n_trials=n_trials)
            table.add_row(
                s, str(res.n_samples), str(res.n_folds), str(res.n_taken),
                _fmt(res.base_win_rate, pct=True), _fmt(res.model_win_rate, pct=True),
                _fmt(res.lift, pct=True), _fmt(res.auc), _fmt(res.sharpe),
                _fmt(res.max_drawdown, pct=True), _fmt(res.profit_factor),
                _fmt(res.deflated_sharpe, ".2f"), _fmt(res.brier, ".3f"),
                _fmt(res.calib_gap, ".3f"))
    console.print(table)
    if record:
        console.print("[dim]Записано в реестр futures_model_runs; чемпионы — geo "
                      "futures-intraday models.[/]")


@futures_intraday_app.command("models")
def futures_intraday_models(
    interval: str = typer.Option(None, "--interval", "-i", help="Фильтр интервала (опц.)."),
    limit: int = typer.Option(15, "--limit", "-n", help="Сколько последних прогонов."),
) -> None:
    """Показать реестр оценок политик (последние прогоны, чемпионы помечены ★)."""
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import FuturesModelRunRepository

    with session_scope() as session:
        rows = FuturesModelRunRepository(session).recent(interval=interval, limit=limit)
        rows = [(r.ts, r.source, r.asset_code, r.interval, r.n_taken, r.lift, r.sharpe,
                 r.deflated_sharpe, r.is_champion) for r in rows]
    if not rows:
        console.print("[yellow]Реестр пуст. Сначала: geo futures-intraday evaluate[/]")
        return
    table = Table(title="Реестр политик (futures_model_runs)")
    for col in ("Время", "Политика", "Актив", "Инт.", "Взято", "Lift", "Sharpe", "DSR", ""):
        table.add_column(col)
    for ts, src, code, itv, taken, lift, shp, dsr, champ in rows:
        table.add_row(f"{ts:%m-%d %H:%M}", src, code or "пулинг", itv, str(taken),
                      _fmt(lift, pct=True), _fmt(shp), _fmt(dsr, ".2f"),
                      "★" if champ else "")
    console.print(table)


@futures_intraday_app.command("pbo")
def futures_intraday_pbo(
    interval: str = typer.Option("1h", "--interval", "-i", help="1m | 10m | 1h | 1d."),
    splits: int = typer.Option(6, "--splits", help="Число purged K-fold блоков."),
) -> None:
    """Пул 4: PBO — вероятность переобучения бэктеста (CSCV по стратегиям, purged K-fold).

    Насколько отбор лучшей стратегии по in-sample переносится на out-of-sample. PBO<0.3 — надёжно;
    >0.5 — выбор по бэктесту не лучше монетки (ловим шум). Главный тест «доказанности» эджа."""
    from geoanalytics.futrader.decisions import SIGNAL_FNS
    from geoanalytics.futrader.evaluation import run_cpcv_pbo
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        res = run_cpcv_pbo(session, sources=list(SIGNAL_FNS), interval=interval, n_splits=splits)
    if res.pbo is None:
        console.print(f"[yellow]{res.note}[/]")
        return
    color = "green" if res.pbo < 0.3 else ("yellow" if res.pbo < 0.5 else "red")
    console.print(f"[bold]PBO = [{color}]{res.pbo:.2f}[/][/] ({res.note})")
    means = ", ".join(f"{s}={v:+.3f}" for s, v in res.oos_sharpe_mean.items())
    console.print(f"  Средний OOS-Sharpe по стратегиям: {means}")
    console.print("  [dim]PBO<0.3 — отбор стратегии надёжен; >0.5 — бэктест ловит шум.[/]")


@futures_intraday_app.command("drift")
def futures_intraday_drift(
    account: str = typer.Option("demo", "--account", help="Бумажный счёт."),
    interval: str = typer.Option("1h", "--interval", "-i", help="Интервал."),
    no_halt: bool = typer.Option(False, "--no-halt", help="Только наблюдать, не взводить halt."),
) -> None:
    """Пул 9/D: live-дрейф чемпиона — PSI признаков, калибровка по бумажным исходам, decay win-rate.

    Сдвиг входных данных (PSI), поломка калибровки или просадка win-rate относительно OOS-ожидания —
    сигнал деградации модели. При жёстком дрейфе взводит kill-switch (если не --no-halt) + алерт."""
    from geoanalytics.futrader.decisions import SIGNAL_FNS
    from geoanalytics.futrader.monitoring import run_drift_monitor
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        reports = run_drift_monitor(session, sources=list(SIGNAL_FNS), account=account,
                                    interval=interval, auto_halt=not no_halt)
    table = Table(title=f"Дрейф чемпионов ({account} {interval})")
    for col in ("Стратегия", "PSI-max", "Признак", "Live-Brier", "CalGap", "WR live",
                "WR ожид.", "Decay", "Сделок", "Halt"):
        table.add_column(col)
    for r in reports:
        table.add_row(
            r.source, _fmt(r.psi_max, ".2f"), r.psi_worst_feature or "—",
            _fmt(r.live_brier, ".3f"), _fmt(r.live_calib_gap, ".3f"),
            _fmt(r.win_rate_live, pct=True), _fmt(r.win_rate_expected, pct=True),
            _fmt(r.win_rate_decay, ".3f"), str(r.n_live_trades),
            "⛔" if r.should_halt else "")
    console.print(table)
    console.print("[dim]PSI>0.25 — заметный сдвиг входов; >0.5/калибр>0.3/decay>0.2 при ≥20 "
                  "сделках → halt.[/]")


@futures_intraday_app.command("paper")
def futures_intraday_paper(
    account: str = typer.Option("demo", "--account", help="Бумажный счёт."),
    interval: str = typer.Option("1h", "--interval", "-i", help="Интервал торговли."),
    cash: float = typer.Option(100_000.0, "--cash", help="Стартовый капитал счёта, ₽."),
    risk: float = typer.Option(1.0, "--risk", help="Целевой риск на сделку, % эквити."),
    max_dd: float = typer.Option(25.0, "--max-dd", help="Лимит просадки (circuit-breaker), %."),
) -> None:
    """Фаза D (T2.5): один бумажный цикл — квалифицированные чемпионы торгуют на свежих барах.

    Гейт качества пускает лишь стратегии с положительным OOS lift+Sharpe и достаточной выборкой
    (см. реестр geo futures-intraday models). Размер — vol-targeting×Келли; circuit-breaker по
    просадке. Реальных ордеров нет — всё на бумажном счёте. Перед запуском: evaluate (чемпионы)."""
    from geoanalytics.futrader.paper import run_paper_cycle
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        res = run_paper_cycle(session, account=account, interval=interval, starting_cash=cash,
                              target_risk_pct=risk, max_dd_pct=max_dd)
    q = ", ".join(res.qualified_strategies) or "—"
    console.print(
        f"[green]✓[/] Бумажный цикл [{account}] {interval}: открыто {res.opened}, "
        f"закрыто {res.closed}, маркеров {res.marked}; квалиф. стратегии: {q}")
    regime = f"режим «{res.regime}»" + (" — входы заблокированы" if res.blocked_regime else "")
    if res.halted:
        console.print(f"[bold red]⛔ KILL-SWITCH: {res.halt_reason}[/] — новые входы заблокированы "
                      "(выходы идут). Снять: geo futures-intraday resume.")
    console.print(
        f"  Гейт отсёк {res.skipped_gate} стратегий, брейкер {res.blocked_breaker}, режим "
        f"{res.blocked_regime}, conviction {res.blocked_conviction}, бюджет {res.blocked_budget}, "
        f"halt {res.blocked_halt}, аномалий {res.anomalies}, устар./выходные {res.blocked_stale}, "
        f"ликвидность {res.blocked_liquidity}, сессия {res.blocked_session}, "
        f"издержки {res.blocked_cost}; {regime}")
    if res.session_flat or res.barrier_exits:
        console.print(
            f"  [yellow]⏰ Дисциплина выхода: барьер {res.barrier_exits} (SL/TP/тайм-стоп), "
            f"флэт к закрытию {res.session_flat} (не держим под закрытие/овернайт)[/]")
    console.print(
        f"  реализ. P&L {res.realized_pnl:+,.0f}₽, нереализ. {res.unrealized_pnl:+,.0f}₽, "
        f"эквити {res.equity:,.0f}₽; просадка {res.drawdown_pct:.1f}%, риск-скейл "
        f"{res.risk_scale:.2f}, маржа {res.gross_margin:,.0f}₽")


@futures_intraday_app.command("risk-status")
def futures_intraday_risk_status(
    account: str = typer.Option("demo", "--account", help="Бумажный счёт."),
) -> None:
    """Пул 9/B: состояние kill-switch счёта + действующие жёсткие лимиты."""
    from geoanalytics.futrader.risk_limits import RiskLimits
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import FuturesRiskStateRepository

    with session_scope() as session:
        st = FuturesRiskStateRepository(session).get(account)
    lim = RiskLimits()
    if st and st.halted:
        console.print(f"[bold red]⛔ ОСТАНОВЛЕН[/] [{account}]: {st.reason or '—'} "
                      f"(с {st.updated_at:%Y-%m-%d %H:%M}). Снять: geo futures-intraday resume.")
    else:
        console.print(f"[green]✓ Активен[/] [{account}] — kill-switch не взведён.")
    console.print(
        f"  Лимиты: дневной убыток {lim.max_daily_loss_pct:.0f}%, брутто-маржа "
        f"{lim.max_gross_margin_pct:.0f}%, позиция ≤{lim.max_position_per_instrument}, "
        f"устаревание бара {lim.max_bar_staleness_hours:.0f}ч, скачок цены "
        f"{lim.max_price_jump_pct:.0f}%")


@futures_intraday_app.command("paper-reset")
def futures_intraday_paper_reset(
    account: str = typer.Option("demo", "--account", help="Бумажный счёт."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Без подтверждения."),
) -> None:
    """Чистый сброс бумажного счёта: позиции + снимки эквити + лог сделок (датасет НЕ трогаем).

    Для рестарта песочницы «с чистого листа» (напр. после legacy-переплеча): маржа→0, транзиентный
    halt снимется сам, эквити вернётся к стартовому. Обучающие свечи/решения сохраняются."""
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import FuturesPaperRepository

    if not yes:
        ok = typer.confirm(f"Сбросить бумажный счёт [{account}] (позиции/эквити/сделки)?")
        if not ok:
            console.print("[dim]Отменено.[/]")
            raise typer.Exit()
    with session_scope() as session:
        deleted = FuturesPaperRepository(session).reset_account(account)
    console.print(
        f"[green]✓[/] Счёт [{account}] сброшен: позиций {deleted['positions']}, снимков эквити "
        f"{deleted['equity']}, сделок {deleted['trades']}. Датасет сохранён. Маржа→0, halt снят.")


@futures_intraday_app.command("resume")
def futures_intraday_resume(
    account: str = typer.Option("demo", "--account", help="Бумажный счёт."),
) -> None:
    """Пул 9/B: снять kill-switch вручную (после разбора причины остановки)."""
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import FuturesRiskStateRepository

    with session_scope() as session:
        repo = FuturesRiskStateRepository(session)
        was = repo.is_halted(account)
        repo.set_state(account, halted=False, reason=None)
    if was:
        console.print(f"[green]✓[/] Kill-switch снят для [{account}] — торговля возобновлена.")
    else:
        console.print(f"[dim]Счёт [{account}] и так не был остановлен.[/]")


@futures_intraday_app.command("paper-status")
def futures_intraday_paper_status(
    account: str = typer.Option("demo", "--account", help="Бумажный счёт."),
    cash: float = typer.Option(100_000.0, "--cash", help="Стартовый капитал (для эквити)."),
    trades: int = typer.Option(0, "--trades", "-t", help="Показать N последних сделок."),
) -> None:
    """Состояние бумажного счёта: открытые позиции, реализованный P&L, эквити."""
    from geoanalytics.analytics.history import _front_futures_secid
    from geoanalytics.futrader.data import fetch_contract_spec
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import FuturesPaperRepository

    with session_scope() as session:
        repo = FuturesPaperRepository(session)
        positions = repo.positions(account)
        realized = sum(p.realized_pnl for p in positions)
        unreal = 0.0
        ptable = Table(title=f"Бумажные позиции [{account}]")
        for col in ("Инстр.", "Инт.", "Стратегия", "Qty", "Сред.", "Послед.",
                    "Реал.P&L", "Нереал."):
            ptable.add_column(col)
        spec_cache: dict = {}
        for p in positions:
            u = 0.0
            if p.net_qty and p.avg_price and p.last_price:
                if p.asset_code not in spec_cache:
                    sid = _front_futures_secid(p.asset_code)
                    spec_cache[p.asset_code] = fetch_contract_spec(sid) if sid else None
                spec = spec_cache[p.asset_code]
                if spec is not None:
                    u = spec.pnl_rub(p.last_price - p.avg_price, p.net_qty)
                    unreal += u
            ptable.add_row(p.asset_code, p.interval, p.source, f"{p.net_qty:+d}",
                           f"{p.avg_price:.2f}" if p.avg_price else "—",
                           f"{p.last_price:.2f}" if p.last_price else "—",
                           f"{p.realized_pnl:+,.0f}", f"{u:+,.0f}")
        if positions:
            console.print(ptable)
        else:
            console.print(f"[yellow]Нет позиций на счёте [{account}]. Сначала: geo "
                          f"futures-intraday paper[/]")
        equity = cash + realized + unreal
        color = "green" if equity >= cash else "red"
        console.print(
            f"Реализованный P&L [bold]{realized:+,.0f}₽[/], нереализованный {unreal:+,.0f}₽; "
            f"эквити [{color}]{equity:,.0f}₽[/] (старт {cash:,.0f}₽)")
        if trades:
            tt = Table(title="Последние бумажные сделки")
            for col in ("Время", "Инстр.", "Стр.", "Действ.", "Qty", "Цена", "P(win)", "P&L"):
                tt.add_column(col)
            for t in repo.recent_trades(account, limit=trades):
                tt.add_row(f"{t.ts:%m-%d %H:%M}", t.asset_code, t.source, t.action,
                           f"{t.signed_qty:+d}", f"{t.price:.2f}",
                           f"{t.p_win:.2f}" if t.p_win is not None else "—",
                           f"{t.realized_pnl:+,.0f}" if t.realized_pnl is not None else "—")
            console.print(tt)


@futures_intraday_app.command("track-record")
def futures_intraday_track_record(
    account: str = typer.Option("demo", "--account", help="Бумажный счёт."),
    cash: float = typer.Option(100_000.0, "--cash", help="Стартовый капитал (база доходности)."),
) -> None:
    """Пул 8: трек-рекорд песочницы — доходность/просадка/Sharpe/win-rate по кривой эквити.

    Накопленная кривая `futures_paper_equity` (снимок/час) + закрытые сделки → метрики, по которым
    судим «доказана ли результативность». Атрибуция P&L по стратегиям и инструментам. Чем дольше
    созревает счёт, тем достовернее цифры (точек больше)."""
    from geoanalytics.futrader.track import track_record
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        rec = track_record(session, account=account, starting_cash=cash)
    if rec.note:
        console.print(f"[yellow]{rec.note}[/]")
    m = rec.metrics
    color = "green" if rec.equity >= rec.starting_cash else "red"
    console.print(
        f"[bold]Трек-рекорд [{account}][/] — эквити [{color}]{rec.equity:,.0f}₽[/] "
        f"(старт {rec.starting_cash:,.0f}₽), доходность {_fmt(m.total_return_pct, '+.2f')}%, "
        f"снимков {m.n_points}")
    console.print(
        f"  реализ. {rec.realized_pnl:+,.0f}₽, нереализ. {rec.unrealized_pnl:+,.0f}₽; "
        f"просадка {rec.drawdown_pct:.1f}%, maxDD {_fmt(m.max_drawdown_pct, '.1f')}%, "
        f"Sharpe {_fmt(m.sharpe, '.2f')}; открытых позиций {rec.open_positions}")
    console.print(
        f"  сделок {m.n_trades}, win-rate {_fmt(m.win_rate, pct=True)}, "
        f"profit-factor {_fmt(m.profit_factor, '.2f')}, ср.прибыль {_fmt(m.avg_win, '+.0f')}₽, "
        f"ср.убыток {_fmt(m.avg_loss, '+.0f')}₽")
    if rec.by_strategy:
        by_s = ", ".join(f"{k} {v:+,.0f}₽" for k, v in sorted(
            rec.by_strategy.items(), key=lambda kv: kv[1], reverse=True))
        console.print(f"  по стратегиям: {by_s}")
    if rec.by_instrument:
        by_i = ", ".join(f"{k} {v:+,.0f}₽" for k, v in sorted(
            rec.by_instrument.items(), key=lambda kv: kv[1], reverse=True))
        console.print(f"  по инструментам: {by_i}")
    risk = rec.risk
    if risk is not None and risk.n_instruments:
        console.print(
            f"  [bold]портфельный риск[/]: VaR95 {_fmt(risk.var_pct, '.2f')}%, ES95 "
            f"{_fmt(risk.es_pct, '.2f')}%; брутто {risk.gross_exposure:,.0f}₽, нетто "
            f"{risk.net_exposure:+,.0f}₽")
        if risk.contributions:
            contrib = ", ".join(f"{k} {v:+.0f}%" for k, v in sorted(
                risk.contributions.items(), key=lambda kv: abs(kv[1]), reverse=True))
            console.print(f"  риск-контрибьюторы: {contrib}")
        if risk.top_correlations:
            corr = ", ".join(f"{pair} {v:+.2f}" for pair, v in risk.top_correlations)
            console.print(f"  топ-корреляции: {corr}")


@app.command("asset-context-accumulate")
def asset_context_accumulate(
    use_llm: bool = typer.Option(False, "--llm",
                                 help="Генерить нарратив через Ollama (по умолчанию — шаблон)."),
) -> None:
    """L5: накопить средне-долгосрочный нарратив (AssetContext) по всем акциям.

    Прогоняет `build_context` по каждой акции и копит версии в asset_context. По умолчанию без
    Ollama (шаблонный нарратив, батч не конкурирует за GPU); `--llm` — с ИИ-разбором."""
    from sqlalchemy import select

    from geoanalytics.context.asset_context import build_context
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset

    with session_scope() as session:
        tickers = list(session.scalars(
            select(Asset.ticker).where(Asset.kind == "share").order_by(Asset.ticker)))
    built = 0
    with console.status(f"Накопление контекста по {len(tickers)} акциям…"):
        for tk in tickers:
            try:
                if build_context(tk, use_llm=use_llm) is not None:
                    built += 1
            except Exception as exc:  # noqa: BLE001 — один тикер не должен ронять батч
                console.print(f"[yellow]{tk}: {exc}[/]")
    console.print(f"[green]Контекст накоплен[/]: {built}/{len(tickers)} акций.")


@app.command("factor-scores")
def factor_scores(
    show: str = typer.Option(None, "--show", "-s", help="Показать факторы тикера (без пересчёта)."),
) -> None:
    """L3: пересчитать кросс-секционные факторы (value/quality/growth/композит) по вселенной акций.

    Без аргументов — считает срез за сегодня, пишет в `factor_scores` и печатает топ по композиту.
    `--show SBER` — только показать свежие факторные ранги тикера."""
    from sqlalchemy import select

    from geoanalytics.analytics.factor_model import (
        factor_scores_for_asset,
        record_factor_scores,
    )
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset

    if show:
        with session_scope() as session:
            asset = session.scalars(select(Asset).where(Asset.ticker == show.upper())).first()
            if asset is None:
                console.print(f"[yellow]Актив {show.upper()} не найден.[/]")
                raise typer.Exit(1)
            ranks = factor_scores_for_asset(session, asset.id)
        if not ranks:
            console.print(f"[yellow]Нет факторных скоров для {show.upper()}. "
                          f"Сначала: geo factor-scores[/]")
            return
        table = Table(title=f"Факторные ранги — {show.upper()}")
        table.add_column("Фактор")
        table.add_column("z-скор", justify="right")
        table.add_column("Перцентиль", justify="right")
        for r in ranks:
            table.add_row(r["label"], f"{r['zscore']:+.2f}σ",
                          f"{r['percentile']:.0f}" if r["percentile"] is not None else "—")
        console.print(table)
        return

    with session_scope() as session:
        n = record_factor_scores(session)
        # топ-5 по композиту за сегодняшний срез
        from geoanalytics.storage.models import FactorScore
        top = session.execute(
            select(Asset.ticker, FactorScore.zscore, FactorScore.percentile)
            .join(FactorScore, FactorScore.asset_id == Asset.id)
            .where(FactorScore.factor == "composite")
            .order_by(FactorScore.zscore.desc()).limit(5)
        ).all()
    console.print(f"[green]Факторный срез записан[/]: строк {n}.")
    if top:
        table = Table(title="Топ по композиту")
        table.add_column("Тикер")
        table.add_column("Композит z", justify="right")
        table.add_column("Перцентиль", justify="right")
        for tk, z, pct in top:
            table.add_row(tk, f"{z:+.2f}σ", f"{pct:.0f}" if pct is not None else "—")
        console.print(table)


@portfolio_app.callback()
def portfolio_main(ctx: typer.Context) -> None:
    """Без сабкоманды — полный отчёт по портфелю."""
    if ctx.invoked_subcommand is not None:
        return
    from geoanalytics.analytics.portfolio import portfolio_report
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        rep = portfolio_report(session)
    if rep.error:
        console.print(f"[yellow]{rep.error}[/]")
        return

    regime_str = f", режим рынка: [bold]{rep.regime}[/]" if rep.regime else ""
    console.print(f"Портфель: [bold]{rep.total_value_rub:,.0f} ₽[/]{regime_str}")

    table = Table(title="Позиции")
    table.add_column("Тикер")
    table.add_column("Кол-во", justify="right")
    table.add_column("Цена", justify="right")
    table.add_column("Стоимость, ₽", justify="right")
    table.add_column("Вес", justify="right")
    table.add_column("P&L", justify="right")
    table.add_column("β рынок", justify="right")
    table.add_column("Давл. 7д", justify="right")
    table.add_column("Сент. EMA", justify="right")
    for p in rep.positions:
        pnl = f"{p.pnl_pct:+.1f}%" if p.pnl_pct is not None else "—"
        beta = f"{p.betas['market']:+.2f}" if "market" in p.betas else "—"
        mom = f"{p.momentum:+.3f}" if p.momentum is not None else "—"
        note = f" [dim]({p.note})[/]" if p.note else ""
        table.add_row(
            f"{p.ticker}{note}", f"{p.quantity:g}",
            f"{p.last_close:.2f}" if p.last_close is not None else "—",
            f"{p.value_rub:,.0f}" if p.value_rub is not None else "—",
            f"{p.weight_pct:.1f}%" if p.weight_pct is not None else "—",
            pnl, beta, f"{p.pressure:.3f}", mom,
        )
    console.print(table)

    risk = Table(title=f"Риск (окно {rep.n_obs} торговых дней)")
    risk.add_column("Метрика")
    risk.add_column("Значение", justify="right")
    if rep.daily_vol_pct is not None:
        risk.add_row("Дневная волатильность", f"{rep.daily_vol_pct:.2f}%")
    if rep.var95_1d_pct is not None:
        risk.add_row("VaR 95% (1д)", f"{rep.var95_1d_pct:.2f}% "
                                     f"({rep.var95_1d_rub:,.0f} ₽)")
    if rep.var99_1d_pct is not None:
        risk.add_row("VaR 99% (1д), ориентир.", f"{rep.var99_1d_pct:.2f}%")
    if rep.max_drawdown_pct is not None:
        risk.add_row("Max просадка (окно)", f"{rep.max_drawdown_pct:.2f}%")
    if risk.row_count:
        console.print(risk)
    else:
        console.print("[yellow]Мало общей истории для риск-метрик.[/]")

    if rep.exposure:
        expo = Table(title="Факторная экспозиция (Σ вес·β)"
                     + (f", ср. R²={rep.avg_r2:.2f}" if rep.avg_r2 is not None else ""))
        expo.add_column("Фактор")
        expo.add_column("β портфеля", justify="right")
        for factor, beta in sorted(rep.exposure.items()):
            expo.add_row(factor, f"{beta:+.3f}")
        console.print(expo)

    if rep.correlations:
        corr = Table(title="Корреляции холдингов")
        corr.add_column("Пара")
        corr.add_column("ρ", justify="right")
        for (a, b), r in sorted(rep.correlations.items()):
            corr.add_row(f"{a} / {b}", f"{r:+.2f}")
        console.print(corr)


@portfolio_app.command("add")
def portfolio_add(
    ticker: str = typer.Argument(..., help="Тикер, напр. SBER."),
    quantity: float = typer.Argument(..., help="Количество (добавляется к позиции)."),
    price: float | None = typer.Option(None, "--price", help="Цена входа (для P&L)."),
) -> None:
    """Добавить/нарастить позицию портфеля."""
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import PortfolioRepository

    with session_scope() as session:
        try:
            pos = PortfolioRepository(session).upsert_position(ticker, quantity, price)
        except ValueError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(code=1) from exc
        if pos is None:
            console.print(f"[red]Актив {ticker.upper()} не найден в БД.[/]")
            raise typer.Exit(code=1)
        qty = pos.quantity
    console.print(f"[green]✓[/] {ticker.upper()}: позиция {qty:g}")


@portfolio_app.command("remove")
def portfolio_remove(
    ticker: str = typer.Argument(..., help="Тикер позиции."),
) -> None:
    """Удалить позицию из портфеля целиком."""
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import PortfolioRepository

    with session_scope() as session:
        removed = PortfolioRepository(session).remove_position(ticker)
    if removed:
        console.print(f"[green]✓[/] {ticker.upper()} удалён из портфеля.")
    else:
        console.print(f"[yellow]{ticker.upper()} в портфеле не было.[/]")


@portfolio_app.command("cash")
def portfolio_cash(
    currency: str = typer.Argument(None, help="Валюта (RUB/USD/EUR/CNY). Пусто — список."),
    amount: float = typer.Argument(None, help="Остаток (перезаписывает; 0/пусто — удалить)."),
) -> None:
    """Кэш/валютные балансы портфеля владельца: показать (без аргументов), задать или удалить.

    `geo portfolio cash` — список; `geo portfolio cash USD 1500` — задать; `geo portfolio cash
    USD 0` — удалить. Оценивается в ₽ по курсу ЦБ (входит в стоимость и снижает риск)."""
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import CashBalanceRepository

    with session_scope() as session:
        repo = CashBalanceRepository(session)
        if currency is None:
            balances = repo.list_balances()
            if not balances:
                console.print("[yellow]Кэш не задан. geo portfolio cash USD 1500[/]")
                return
            table = Table(title="Кэш/валюта")
            table.add_column("Валюта")
            table.add_column("Сумма", justify="right")
            for ccy, amt in balances:
                table.add_row(ccy, f"{amt:,.2f}")
            console.print(table)
            return
        if amount is None or amount <= 0:
            removed = repo.remove(currency)
            console.print(f"[green]✓[/] {currency.upper()} удалён." if removed
                          else f"[yellow]{currency.upper()} не было.[/]")
            return
        repo.set_balance(currency, amount)
    console.print(f"[green]✓[/] {currency.upper()}: остаток {amount:,.2f}")


@portfolio_app.command("snapshot")
def portfolio_snapshot() -> None:
    """Записать дневной снимок стоимости каждого портфеля (владелец + бот-юзеры).

    Обычно делается ежедневным джобом scheduler; здесь — ручной прогон/сидирование истории
    (графики «Стоимость во времени»/«P&L» переключаются на факт при ≥2 снимках)."""
    from geoanalytics.analytics.portfolio import snapshot_portfolios
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        n = snapshot_portfolios(session)
    console.print(f"[green]✓[/] Снимки записаны: портфелей {n}")


@app.command()
def candles(
    ticker: str = typer.Argument(..., help="Тикер актива."),
    days: int = typer.Option(90, "--days", "-n", help="Окно последних баров."),
    trend: int = typer.Option(10, "--trend",
                              help="Окно SMA для контекста тренда."),
) -> None:
    """Свечные паттерны Нисона по дневным барам (детекторы + контекст тренда).

    Доджи и перевёрнутый молот — информационные (в торговый сигнал стратегии
    `candles` не входят); проверка прибыльности — `geo walkforward -S candles`."""
    from geoanalytics.analytics.candlesticks import patterns_for_asset

    hits = patterns_for_asset(ticker, days=days, trend=trend)
    if hits is None:
        console.print(f"[red]Актив {ticker.upper()} не найден.[/]")
        raise typer.Exit(code=1)
    if not hits:
        console.print(f"За последние {days} баров паттернов не найдено.")
        return
    table = Table(title=f"Свечные паттерны {ticker.upper()} (посл. {days} баров)")
    table.add_column("Дата")
    table.add_column("Паттерн")
    table.add_column("Сигнал")
    arrows = {1: "[green]бычий ↑[/]", -1: "[red]медвежий ↓[/]", 0: "[dim]нейтрален[/]"}
    for day, hit in hits:
        table.add_row(day.strftime("%d.%m.%Y"), hit.name, arrows[hit.direction])
    console.print(table)


@app.command()
def whatif(
    market: float | None = typer.Option(None, "--market",
                                        help="Шок IMOEX, % (напр. -5)."),
    usd: float | None = typer.Option(None, "--usd", help="Шок USD/RUB, %."),
    brent: float | None = typer.Option(None, "--brent", help="Шок Brent, %."),
    gold: float | None = typer.Option(None, "--gold",
                                      help="Шок золота (учётная цена ЦБ, ₽/г), %."),
    ticker: str | None = typer.Option(None, "--ticker", "-t",
                                      help="Один актив вместо портфеля."),
    window: int = typer.Option(250, "--window", "-w", help="Окно оценки бет."),
) -> None:
    """Сценарий «что-если» (J4): портфель при шоках факторов.

    Ставочный сценарий выражайте через --market/--usd: ставка не фактор
    атрибуции (решений ЦБ ~8/год — регрессии не на чем учиться)."""
    from geoanalytics.analytics.whatif import whatif_asset, whatif_portfolio
    from geoanalytics.storage.db import session_scope

    shocks: dict[str, float] = {}
    if market is not None:
        shocks["market"] = market
    if usd is not None:
        shocks["usd_rub"] = usd
    if brent is not None:
        shocks["brent"] = brent
    if gold is not None:
        shocks["gold"] = gold
    if not shocks:
        console.print("[red]Задайте хотя бы один шок: "
                      "--market / --usd / --brent / --gold.[/]")
        raise typer.Exit(code=1)

    with session_scope() as session:
        r = (whatif_asset(session, ticker, shocks, window=window) if ticker
             else whatif_portfolio(session, shocks, window=window))
    if r.error:
        console.print(f"[yellow]{r.error}[/]")
        raise typer.Exit(code=1)

    shocks_str = ", ".join(f"{f} {v:+g}%" for f, v in r.shocks_pct.items())
    console.print(f"Сценарий: [bold]{shocks_str}[/]")
    table = Table(title="Ожидаемая реакция")
    table.add_column("Тикер")
    factors = sorted({f for a in r.assets for f in a.contributions_pct})
    for f in factors:
        table.add_column(f, justify="right")
    table.add_column("Итог", justify="right")
    table.add_column("R²", justify="right")
    for a in sorted(r.assets, key=lambda x: x.expected_move_pct):
        color = "red" if a.expected_move_pct < 0 else "green"
        table.add_row(
            a.ticker,
            *(f"{a.contributions_pct[f]:+.2f}%" if f in a.contributions_pct
              else "—" for f in factors),
            f"[{color}]{a.expected_move_pct:+.2f}%[/]", f"{a.r2:.2f}",
        )
    console.print(table)
    if r.portfolio_move_pct is not None:
        color = "red" if r.portfolio_move_pct < 0 else "green"
        console.print(
            f"Портфель ({r.total_value_rub:,.0f} ₽): "
            f"[bold {color}]{r.portfolio_move_pct:+.2f}%[/] = "
            f"[bold {color}]{r.portfolio_pnl_rub:+,.0f} ₽[/]"
        )
    console.print(Panel("\n".join(f"• {c}" for c in r.caveats),
                        title="Оговорки", border_style="yellow"))


db_app = typer.Typer(help="Управление БД.")
app.add_typer(db_app, name="db")


@db_app.command("upgrade")
def db_upgrade(revision: str = typer.Argument("head")) -> None:
    """Применить миграции Alembic до указанной ревизии."""
    from alembic.config import Config

    from alembic import command

    cfg = Config("alembic.ini")
    command.upgrade(cfg, revision)
    console.print(f"[green]Миграции применены до {revision}[/]")


@db_app.command("seed")
def db_seed() -> None:
    """Заполнить справочник крупных эмитентов РФ (для entity-linking)."""
    from geoanalytics.storage.seed import seed_database

    added = seed_database()
    console.print(f"[green]Справочник заполнен[/]: добавлено активов — {added}")


@app.command("run-scheduler")
def run_scheduler() -> None:
    """Запустить периодический сбор данных (заготовка под оркестрацию)."""
    from geoanalytics.orchestration.scheduler import run

    run()


@app.command("run-futrader")
def run_futrader() -> None:
    """Трек 2: автономный торговый цикл (интрадей-paper + дневная петля train/eval/PBO/drift).

    Чисто numeric — отдельная служба от scheduler (свой потолок памяти, не разгоняет scheduler).
    Raspberry-Pi-ready: на Pi запускается с GEO_DB_HOST → Postgres главной машины, без правок кода.
    """
    from geoanalytics.orchestration.futrader_runner import run_futrader_loop

    run_futrader_loop()


@app.command("run-bot")
def run_bot() -> None:
    """Запустить входящий Telegram-бот (Волна 5a): /ask /asset /portfolio /alerts.

    Long-poll getUpdates, отвечает только chat_id из allowlist (GEO_TELEGRAM_CHAT_ID).
    Включить: GEO_TELEGRAM_BOT_ENABLED=true. Отдельная служба от scheduler/dashboard.
    """
    from geoanalytics.bot.service import run

    run()


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Адрес для прослушивания."),
    port: int = typer.Option(8800, "--port", "-p", help="Порт (по умолчанию 8800)."),
    reload: bool = typer.Option(False, "--reload", help="Авто-перезапуск при правках (dev)."),
) -> None:
    """Запустить REST API (FastAPI). Документация — на /docs.

    Требуются extras: pip install -e ".[api]".
    """
    try:
        import uvicorn
    except ImportError as exc:
        console.print('[red]Не установлен uvicorn. Установите: pip install -e ".[api]"[/]')
        raise typer.Exit(code=1) from exc

    console.print(f"[green]API:[/] http://{host}:{port}  (docs: /docs)")
    uvicorn.run("geoanalytics.api.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
