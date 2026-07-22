"""Команды системных служб: проверка здоровья, алерты, миграции БД, планировщик, бот и сервер."""

from __future__ import annotations

import typer
from rich.table import Table

from geoanalytics.cli.common import app, console

db_app = typer.Typer(help="Управление БД.")
app.add_typer(db_app, name="db")


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
