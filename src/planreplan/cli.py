"""Command-line entry point.

The CLI is the harness the whole loop is built against (ADR-5): every phase
lands a verb here with ``--help`` text before it counts as done. Phase 0
ships the skeleton and ``--version``; ``validate``, ``cpm``, ``solve``,
``check``, ``ingest``, ``status``, ``repair``, ``report``, ``tell``, and
``explain`` arrive with the phases that implement them (``docs/06-roadmap.md``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from planreplan import __version__
from planreplan.cpm import analyse
from planreplan.domain import (
    PayClass,
    ProjectIndex,
    ProjectValidationError,
    overtime_report,
    validate_project,
)
from planreplan.io import SchemaVersionError, load_project

app = typer.Typer(
    name="planreplan",
    help=(
        "Construction planning and replanning. Plans are solved deterministically, "
        "monitored against reality, and repaired with a reported tradeoff between "
        "schedule stability and schedule optimality."
    ),
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    """Print the version and exit, before any other option is processed."""
    if value:
        typer.echo(f"planreplan {__version__}")
        raise typer.Exit


@app.callback(invoke_without_command=True)
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show the installed version and exit.",
    ),
) -> None:
    """Root command group; subcommands are registered by later phases."""


ProjectArg = Annotated[
    Path,
    typer.Argument(
        metavar="PROJECT",
        help="Project directory, or the project.json inside one.",
        show_default=False,
    ),
]


def _load_and_validate(project: Path) -> ProjectIndex:
    """Shared front door for the commands that need a usable project."""
    try:
        loaded = load_project(project)
    except (FileNotFoundError, SchemaVersionError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    except ValidationError as exc:
        typer.secho(f"{project}: malformed project file", fg=typer.colors.RED, err=True)
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    try:
        return validate_project(loaded)
    except ProjectValidationError as exc:
        count = len(exc.problems)
        typer.secho(
            f"{count} problem{'s' if count != 1 else ''} in {loaded.id!r}:",
            fg=typer.colors.RED,
            err=True,
        )
        for problem in exc.problems:
            typer.echo(f"  - {problem}", err=True)
        raise typer.Exit(1) from exc


@app.command()
def cpm(
    project: ProjectArg,
    dates: Annotated[
        bool, typer.Option("--dates/--ticks", help="Show wall-clock dates instead of ticks.")
    ] = True,
) -> None:
    """Compute early and late dates, floats, and the critical path.

    Float is reported in working ticks of each task's effective calendar, not
    elapsed ones: a weekend is not float anybody can spend, and float is only
    comparable to duration when the two share a unit.
    """
    index = _load_and_validate(project)
    result = analyse(index)
    axis = index.project.axis

    def when(tick: int) -> str:
        return axis.to_datetime(tick).strftime("%Y-%m-%d %H:%M") if dates else str(tick)

    width = max((len(t) for t in index.leaves), default=4)
    typer.echo(
        f"{'task':<{width}}  {'early start':>16}  {'early finish':>16}  {'TF':>5}  {'FF':>5}"
    )
    for task_id in index.leaves:
        entry = result.tasks[task_id]
        marker = "*" if entry.is_critical else " "
        typer.echo(
            f"{task_id:<{width}}  {when(entry.early_start):>16}  "
            f"{when(entry.early_finish):>16}  {entry.total_float:>5}  {entry.free_float:>5}{marker}"
        )
    typer.echo()
    typer.echo(f"project finish  {when(result.project_finish)}")
    typer.secho(f"critical path   {' -> '.join(result.critical_path)}", fg=typer.colors.YELLOW)


@app.command()
def overtime(project: ProjectArg) -> None:
    """Report regular and premium time per resource per pay week.

    Spans come from the CPM early dates, which is the earliest anything can be
    said about overtime before a solver produces a real schedule.
    """
    index = _load_and_validate(project)
    reports = overtime_report(index, analyse(index).spans())
    if not reports:
        typer.echo("No resourced work; nothing to report.")
        return

    axis = index.project.axis
    for report in reports:
        week = axis.to_datetime(report.week_start).strftime("%Y-%m-%d")
        typer.echo(f"{report.resource_id}  week of {week}")
        typer.echo(f"    regular   {report.regular_ticks:>5}")
        for pay_class in PayClass:
            ticks = report.premium_ticks.get(pay_class)
            if ticks:
                typer.echo(f"    {pay_class.value:<9} {ticks:>5}")
        typer.echo(f"    total     {report.total_ticks:>5}")


@app.command()
def validate(project: ProjectArg) -> None:
    """Check a project against the domain rules, reporting every problem found.

    Exits 1 when the project is invalid and 2 when it cannot be read at all,
    so a caller can tell "this plan is wrong" from "there is no plan here".
    """
    index = _load_and_validate(project)
    loaded = index.project
    typer.secho(f"{loaded.id!r} is valid.", fg=typer.colors.GREEN)
    typer.echo(f"  tasks       {len(loaded.tasks)} ({len(index.leaves)} leaf)")
    typer.echo(f"  links       {len(index.leaf_dependencies)} between leaves")
    typer.echo(f"  resources   {len(loaded.resources)}")
    typer.echo(f"  calendars   {len(loaded.calendars)}")
    typer.echo(f"  horizon     {index.horizon} ticks at {loaded.axis.ticks_per_day}/day")


if __name__ == "__main__":  # pragma: no cover
    app()
