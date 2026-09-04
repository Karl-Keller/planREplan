"""Command-line entry point.

The CLI is the harness the whole loop is built against (ADR-5): every phase
lands a verb here with ``--help`` text before it counts as done. Phase 0
ships the skeleton and ``--version``; ``validate``, ``cpm``, ``solve``,
``check``, ``ingest``, ``status``, ``repair``, ``report``, ``tell``, and
``explain`` arrive with the phases that implement them (``docs/06-roadmap.md``).
"""

from __future__ import annotations

import typer

from planreplan import __version__

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


if __name__ == "__main__":  # pragma: no cover
    app()
