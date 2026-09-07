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
    Schedule,
    check_schedule,
    overtime_report,
    validate_project,
)
from planreplan.io import (
    SchemaVersionError,
    list_schedules,
    load_project,
    load_schedule,
    save_schedule,
)

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
def solve(
    project: ProjectArg,
    seconds: Annotated[float, typer.Option("--seconds", help="Solver time limit.", min=0.1)] = 10.0,
    seed: Annotated[int, typer.Option(help="CP-SAT random seed.")] = 0,
    workers: Annotated[
        int, typer.Option(help="Search workers. More is faster and less reproducible.", min=1)
    ] = 1,
    save: Annotated[
        bool, typer.Option("--save/--no-save", help="Store the schedule in the project.")
    ] = False,
) -> None:
    """Produce a resource-feasible schedule minimising makespan.

    Reports the status honestly: OPTIMAL and FEASIBLE come from CP-SAT, while
    UNKNOWN means the time limit expired and the schedule shown is the greedy
    serial one, which is feasible but unproven.
    """
    # Imported here, not at module scope: design rule 1 means `validate` and
    # `cpm` must work on an install without OR-Tools, and a top-level import
    # would break both to serve this one command.
    try:
        from planreplan.solve import CpSatScheduler, SolveError, SolveOptions
    except ImportError as exc:  # pragma: no cover - exercised by the core-only CI job
        typer.secho(
            "solving needs OR-Tools, which this install does not have. "
            'Install it with: pip install "planreplan[solve]"',
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2) from exc

    index = _load_and_validate(project)
    options = SolveOptions(time_limit_seconds=seconds, seed=seed, workers=workers)
    try:
        result = CpSatScheduler(options).solve(index)
    except SolveError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    axis = index.project.axis
    relaxed = analyse(index).project_finish
    finish = result.schedule.project_finish
    colour = typer.colors.GREEN if result.is_optimal else typer.colors.YELLOW
    typer.secho(f"{result.status}  in {result.wall_time:.2f}s", fg=colour)
    if not result.is_proven:
        typer.secho(
            "  time limit reached; showing the greedy serial schedule, which is "
            "feasible but not proven optimal",
            fg=typer.colors.YELLOW,
        )
    typer.echo(f"  finish       {axis.to_datetime(finish):%Y-%m-%d %H:%M}  ({finish} ticks)")
    typer.echo(f"  resource-free bound  {relaxed} ticks (CPM, ignores capacities)")
    typer.echo(f"  cost of contention   {finish - relaxed} ticks")
    if result.is_proven and not result.is_optimal:
        typer.echo(f"  optimality gap       {result.gap} ticks")
    typer.echo(f"  schedule     {result.schedule.id}")
    if save:
        path = save_schedule(result.schedule, Path(project))
        typer.echo(f"  saved        {path}")


@app.command()
def check(
    project: ProjectArg,
    schedule_id: Annotated[
        str | None,
        typer.Argument(
            metavar="[SCHEDULE]",
            help="Stored schedule id. Defaults to the only one, if there is only one.",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Verify a stored schedule against the project's rules.

    Needs no solver: checking a concrete schedule is a linear sweep over
    coverage, calendars, precedence, capacity, and the data date. Exits 1 if
    the schedule is not executable as written.
    """
    index = _load_and_validate(project)
    stored = list_schedules(Path(project))
    if schedule_id is None:
        if len(stored) != 1:
            typer.secho(
                f"name a schedule; {len(stored)} are stored" if stored else "no schedules stored",
                fg=typer.colors.RED,
                err=True,
            )
            for identifier in stored:
                typer.echo(f"  {identifier}", err=True)
            raise typer.Exit(2)
        schedule_id = stored[0]

    try:
        plan: Schedule = load_schedule(Path(project), schedule_id)
    except (FileNotFoundError, ValueError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    violations = check_schedule(index, plan)
    if not violations:
        typer.secho(f"{schedule_id} is executable as written.", fg=typer.colors.GREEN)
        return
    count = len(violations)
    typer.secho(
        f"{count} violation{'s' if count != 1 else ''} in {schedule_id}:",
        fg=typer.colors.RED,
        err=True,
    )
    for violation in violations:
        typer.echo(f"  [{violation.kind}] {violation.detail}", err=True)
    raise typer.Exit(1)


@app.command()
def overtime(project: ProjectArg) -> None:
    """Report regular and premium time per resource per pay week.

    Two columns, answering two questions. *ticks* is wall-clock engagement of
    the resource, which is what a labour agreement's per-person thresholds are
    stated against. *person* weights each tick by the units actually engaged,
    which is what a cost is built from.

    The schedule comes from the CPM early dates, the earliest anything can be
    said about overtime before a solver accounts for resource contention.
    """
    index = _load_and_validate(project)
    reports = overtime_report(index, analyse(index).to_schedule())
    if not reports:
        typer.echo("No resourced work; nothing to report.")
        return

    axis = index.project.axis
    for report in reports:
        week = axis.to_datetime(report.week_start).strftime("%Y-%m-%d")
        typer.echo(f"{report.resource_id}  week of {week}")
        typer.echo(f"    {'':<14}{'ticks':>7}{'person':>8}")
        typer.echo(f"    {'regular':<14}{report.regular_ticks:>7}{report.regular_person_ticks:>8}")
        for pay_class in PayClass:
            ticks = report.premium_ticks.get(pay_class)
            if ticks:
                person = report.premium_person_ticks.get(pay_class, 0)
                typer.echo(f"    {pay_class.value:<14}{ticks:>7}{person:>8}")
        typer.echo(f"    {'total':<14}{report.total_ticks:>7}{report.total_person_ticks:>8}")


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
