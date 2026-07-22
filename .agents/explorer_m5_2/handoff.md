# Handoff Report - Explorer M5-2: CLI Dependency Graph & Modularization Strategy

## 1. Observation
- **Target File**: `/home/ijstt/News/src/geoanalytics/cli.py` (2,696 lines, 81 command functions, 7 Typer app instantiations).
- **Typer Apps Cataloged**:
  - `app = typer.Typer(help='geoanalytics — аналитика экономики и геополитики (рынок РФ).', no_args_is_help=True)` (L23)
  - `portfolio_app = typer.Typer(help='Виртуальный портфель (J1): позиции, риск, экспозиция.', invoke_without_command=True)` (L1404)
  - `fundamentals_app = typer.Typer(help='Фундаменталка эмитентов из отчётов (H5).')` (L1411)
  - `segments_app = typer.Typer(help='Сегменты выручки эмитента (L2: состав компании).')` (L1525)
  - `futures_intraday_app = typer.Typer(help='Интрадей-данные и симулятор фьючерсов FORTS (Трек 2 / T2.1–T2.2).')` (L1594)
  - `futures_depth_app = typer.Typer(help='Захват стакана (L2 depth) фьючерсов FORTS (Трек 2, миграция 0037).')` (L1598)
  - `db_app = typer.Typer(help='Управление БД.')` (L2618)
- **Shared Helpers & Console Instances**:
  - `console = Console()` (L27, referenced by 39 commands creating `rich.table.Table`)
  - `_rich_link(title: str, url: str) -> str` (L1279-L1281)
  - `_fmt(val: float | None, fmt: str = ".4f") -> str` (L1910-L1913)
- **Pytest Baseline Execution**:
  - Command: `source .venv/bin/activate && pytest tests/`
  - Output: `1243 passed in 51.29s` (100% pass rate).
- **Entry Point Contract**: `pyproject.toml` line 68: `geo = "geoanalytics.cli:app"`.

---

## 2. Logic Chain

1. **Monolithic Overcrowding Observation**: `src/geoanalytics/cli.py` has 2,696 lines, violating the project requirement that no single file exceed 600 lines.
2. **Helper Dependency Analysis**: 39 command functions format output with `rich.table.Table` or `console.print`, and functions like `backtest`, `drift`, `evaluate` invoke `_fmt` while `asset` invokes `_rich_link`. Placing these shared tools in `src/geoanalytics/cli/common.py` allows all domain submodules to import them cleanly without circular dependencies.
3. **Domain Classification**: The 81 CLI command functions naturally partition into 10 logical domain submodules (`common`, `ingest`, `nlp`, `analytics`, `alerts`, `backtest`, `fundamentals`, `futures_depth`, `futures_intraday`, `portfolio`, `system`).
4. **Line Budget Verification**: The largest submodule (`futures_intraday.py`) contains ~580 lines, and `analytics.py` contains ~550 lines. All submodules strictly remain below the 600 line limit.
5. **Dispatcher Architecture**: Refactoring `cli.py` to import domain submodules and sub-apps reduces `cli.py` to ~75 lines while preserving entry point `geoanalytics.cli:app` identically.

---

## 3. Caveats
- No code modification was performed during this investigation (strictly read-only).
- Implementation phase must ensure that all sub-app registrations (`add_typer`) match exact CLI names (`portfolio`, `fundamentals`, `segments`, `futures-intraday`, `futures-depth`, `db`) and that decorator names and help texts are identical.

---

## 4. Conclusion
`src/geoanalytics/cli.py` is fully cataloged and ready for modular extraction into `src/geoanalytics/cli/`.
Extracted submodules will all be under 600 lines, and `src/geoanalytics/cli.py` will serve as a lightweight entry point dispatcher of ~75 lines. The public entry point contract `geo = "geoanalytics.cli:app"` will remain 100% compatible.

---

## 5. Verification Method

To verify the investigation and baseline:
1. Inspect the catalog and line budget in `/home/ijstt/News/.agents/explorer_m5_2/analysis.md`.
2. Run baseline pytest:
   ```bash
   source .venv/bin/activate && pytest tests/
   ```
   Expect: 1,243 tests pass (100%).
3. Verify CLI entry point defined in `pyproject.toml`:
   ```bash
   grep 'geo =' pyproject.toml
   ```
   Expect: `geo = "geoanalytics.cli:app"`
