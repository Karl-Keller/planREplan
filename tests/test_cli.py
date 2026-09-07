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


# -- validate --------------------------------------------------------------


def _demo_project(**overrides):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from planreplan.domain import Calendar, DayOfWeek, Project, Shift, Task, TimeAxis

    calendar = Calendar(
        id="5x8",
        week_pattern=dict.fromkeys(
            tuple(DayOfWeek)[:5], (Shift(name="day", start_minute=480, end_minute=960),)
        ),
    )
    base = {
        "id": "demo",
        "axis": TimeAxis(epoch=datetime(2026, 9, 7, tzinfo=ZoneInfo("America/New_York"))),
        "calendars": (calendar,),
        "default_calendar_id": "5x8",
        "tasks": (Task(id="a", duration=16),),
    }
    return Project(**{**base, **overrides})


def test_validate_reports_a_valid_project(tmp_path):
    from planreplan.io import save_project

    save_project(_demo_project(), tmp_path / "p")
    result = runner.invoke(app, ["validate", str(tmp_path / "p")])
    assert result.exit_code == 0
    assert "is valid" in plain(result.stdout)


def test_validate_lists_every_problem_and_exits_one(tmp_path):
    import json

    from planreplan.io import save_project

    path = save_project(_demo_project(), tmp_path / "p")
    raw = json.loads(path.read_text())
    raw["default_calendar_id"] = "ghost"
    raw["tasks"][0]["duration"] = 0
    path.write_text(json.dumps(raw))

    result = runner.invoke(app, ["validate", str(tmp_path / "p")])
    assert result.exit_code == 1
    assert "2 problems" in plain(result.output)


def test_an_unreadable_project_exits_two_not_one(tmp_path):
    """A caller can tell "this plan is wrong" from "there is no plan here"."""
    assert runner.invoke(app, ["validate", str(tmp_path / "absent")]).exit_code == 2


def test_validate_is_documented(colour):
    result = runner.invoke(app, ["validate", "--help"])
    assert result.exit_code == 0
    assert "PROJECT" in plain(result.stdout)


# -- cpm and overtime ------------------------------------------------------


def _resourced_project():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from planreplan.domain import (
        Assignment,
        Calendar,
        DayOfWeek,
        Dependency,
        PayRules,
        Project,
        Resource,
        Shift,
        Task,
        TimeAxis,
    )

    calendar = Calendar(
        id="5x8",
        week_pattern=dict.fromkeys(
            tuple(DayOfWeek)[:5], (Shift(name="day", start_minute=480, end_minute=960),)
        ),
    )
    return Project(
        id="foundation",
        axis=TimeAxis(epoch=datetime(2026, 9, 7, tzinfo=ZoneInfo("America/New_York"))),
        calendars=(calendar,),
        default_calendar_id="5x8",
        pay_rules=(PayRules(id="cba"),),
        default_pay_rules_id="cba",
        tasks=(Task(id="a", duration=8), Task(id="b", duration=8), Task(id="c", duration=8)),
        dependencies=(Dependency(predecessor_id="a", successor_id="b"),),
        resources=(Resource(id="crew", capacity=4),),
        assignments=(Assignment(task_id="a", resource_id="crew"),),
    )


def test_cpm_reports_dates_and_the_critical_path(tmp_path):
    from planreplan.io import save_project

    save_project(_resourced_project(), tmp_path / "p")
    result = runner.invoke(app, ["cpm", str(tmp_path / "p")])
    assert result.exit_code == 0
    output = plain(result.stdout)
    assert "critical path" in output
    assert "2026-09-07" in output  # dates by default


def test_cpm_can_report_raw_ticks(tmp_path):
    from planreplan.io import save_project

    save_project(_resourced_project(), tmp_path / "p")
    output = plain(runner.invoke(app, ["cpm", str(tmp_path / "p"), "--ticks"]).stdout)
    assert "2026-" not in output
    assert "project finish" in output


def test_overtime_reports_per_resource_weeks(tmp_path):
    from planreplan.io import save_project

    save_project(_resourced_project(), tmp_path / "p")
    result = runner.invoke(app, ["overtime", str(tmp_path / "p")])
    assert result.exit_code == 0
    assert "crew" in plain(result.stdout)


def test_overtime_says_so_when_nothing_is_resourced(tmp_path):
    from planreplan.io import save_project

    project = _resourced_project().model_copy(update={"assignments": ()})
    save_project(project, tmp_path / "p")
    assert "nothing to report" in plain(
        runner.invoke(app, ["overtime", str(tmp_path / "p")]).stdout
    )


def test_cpm_on_an_invalid_project_exits_one(tmp_path):
    import json

    from planreplan.io import save_project

    path = save_project(_resourced_project(), tmp_path / "p")
    raw = json.loads(path.read_text())
    raw["tasks"][0]["duration"] = 0
    path.write_text(json.dumps(raw))
    assert runner.invoke(app, ["cpm", str(tmp_path / "p")]).exit_code == 1


@pytest.mark.parametrize("command", ["validate", "cpm", "overtime"])
def test_every_phase_one_command_is_documented(command, colour):
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0
    assert "PROJECT" in plain(result.stdout)
