"""Phase 0 acceptance: the CLI exists, reports its version, and documents itself."""

from typer.testing import CliRunner

from planreplan import __version__
from planreplan.cli import app

runner = CliRunner()


def test_version_flag_reports_the_installed_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"planreplan {__version__}" in result.stdout


def test_short_version_flag_matches():
    assert runner.invoke(app, ["-V"]).stdout == runner.invoke(app, ["--version"]).stdout


def test_help_is_available_and_describes_the_tool():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "replanning" in result.stdout.lower()


def test_bare_invocation_shows_help_rather_than_failing():
    """no_args_is_help: an empty invocation must teach, not error out."""
    result = runner.invoke(app, [])
    assert "--version" in result.stdout


def test_version_is_a_three_part_string():
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
