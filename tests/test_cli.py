"""Phase 0 acceptance: the CLI exists, reports its version, and documents itself."""

import re

import pytest
from typer.testing import CliRunner

from planreplan import __version__
from planreplan.cli import app

runner = CliRunner()

#: Rich style codes. Help output is rendered by Rich, which injects them
#: *inside* option names when it believes the stream supports colour. That is
#: how ``--version`` stopped being a substring of its own help text on CI while
#: passing in a local shell. Assertions about content must not depend on it.
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    return ANSI.sub("", text)


@pytest.fixture(params=[False, True], ids=["mono", "colour"])
def colour(request, monkeypatch):
    """Exercise help output both with and without Rich colour.

    GitHub Actions presents a colour-capable environment, so a test that only
    ever saw a plain local terminal was guarding nothing. Parametrising makes
    the CI condition reproducible on a laptop.
    """
    if request.param:
        monkeypatch.setenv("FORCE_COLOR", "1")
    else:
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        monkeypatch.setenv("TERM", "dumb")
    return request.param


def test_the_colour_fixture_actually_changes_the_rendering(colour):
    """Guard the guard: if this stops holding, the colour case is a no-op."""
    assert bool(ANSI.search(runner.invoke(app, ["--help"]).stdout)) is colour


def test_version_flag_reports_the_installed_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"planreplan {__version__}" in plain(result.stdout)


def test_short_version_flag_matches():
    assert runner.invoke(app, ["-V"]).stdout == runner.invoke(app, ["--version"]).stdout


def test_help_is_available_and_describes_the_tool(colour):
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "replanning" in plain(result.stdout).lower()


def test_bare_invocation_shows_help_rather_than_failing(colour):
    """no_args_is_help: an empty invocation must teach, not error out."""
    result = runner.invoke(app, [])
    assert "--version" in plain(result.stdout)


def test_version_is_a_three_part_string():
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
