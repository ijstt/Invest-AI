"""Общие хелперы, инстанс Typer и Console для CLI geoanalytics."""

from __future__ import annotations

import sys
from pathlib import Path

_root = str(Path(__file__).resolve().parents[3])
if _root not in sys.path:
    sys.path.insert(0, _root)

import typer
from rich.console import Console

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


def _rich_link(text: str, url: str | None) -> str:
    """Кликабельная ссылка для rich-панели; без url — простой текст."""
    return f"[link={url}]{text}[/link]" if url else text


def _fmt(v, spec=".3f", pct=False):
    if v is None:
        return "—"
    return f"{v:+.1%}" if pct else f"{v:{spec}}"
