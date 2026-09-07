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
from planreplan.domain import ProjectValidationError, validate_project
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


@app.command()
def validate(project: ProjectArg) -> None:
    """Check a project against the domain rules, reporting every problem found.

    Exits 1 when the project is invalid and 2 when it cannot be read at all,
    so a caller can tell "this plan is wrong" from "there is no plan here".
    """
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
        index = validate_project(loaded)
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

    typer.secho(f"{loaded.id!r} is valid.", fg=typer.colors.GREEN)
    typer.echo(f"  tasks       {len(loaded.tasks)} ({len(index.leaves)} leaf)")
    typer.echo(f"  links       {len(index.leaf_dependencies)} between leaves")
    typer.echo(f"  resources   {len(loaded.resources)}")
    typer.echo(f"  calendars   {len(loaded.calendars)}")
    typer.echo(f"  horizon     {index.horizon} ticks at {loaded.axis.ticks_per_day}/day")


if __name__ == "__main__":  # pragma: no cover
    app()
