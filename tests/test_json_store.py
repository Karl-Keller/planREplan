"""Project persistence: layout, schema version, and lossless round-trips."""

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from hypothesis import HealthCheck, given, settings

from planreplan.domain import (
    Calendar,
    CalendarException,
    DayOfWeek,
    Project,
    Shift,
    Task,
    TimeAxis,
    validate_project,
)
from planreplan.io import (
    EVENTS_FILE,
    PROJECT_FILE,
    SCHEDULES_DIR,
    SCHEMA_VERSION,
    SchemaVersionError,
    load_project,
    save_project,
)
from strategies import projects

NY = ZoneInfo("America/New_York")
WEEKDAYS = tuple(DayOfWeek)[:5]
DAY = (Shift(name="day", start_minute=8 * 60, end_minute=16 * 60),)


def simple(**overrides) -> Project:
    base = {
        "id": "p",
        "axis": TimeAxis(epoch=datetime(2026, 9, 7, tzinfo=NY)),
        "calendars": (Calendar(id="5x8", week_pattern=dict.fromkeys(WEEKDAYS, DAY)),),
        "default_calendar_id": "5x8",
        "tasks": (Task(id="a", duration=8),),
    }
    return Project(**{**base, **overrides})


def test_saving_creates_the_documented_directory_layout(tmp_path):
    """The layout is a fact on disk rather than a promise in a document."""
    save_project(simple(), tmp_path / "proj")
    assert (tmp_path / "proj" / PROJECT_FILE).is_file()
    assert (tmp_path / "proj" / SCHEDULES_DIR).is_dir()
    assert (tmp_path / "proj" / EVENTS_FILE).is_file()


def test_the_file_is_readable_and_diffable(tmp_path):
    path = save_project(simple(), tmp_path / "proj")
    text = path.read_text()
    assert text.endswith("\n")
    assert '\n  "' in text  # indented, not one line
    assert json.loads(text)["schema_version"] == SCHEMA_VERSION


def test_a_project_round_trips(tmp_path):
    original = simple()
    save_project(original, tmp_path / "proj")
    assert load_project(tmp_path / "proj") == original


def test_the_project_file_may_be_named_directly(tmp_path):
    save_project(simple(), tmp_path / "proj")
    assert load_project(tmp_path / "proj" / PROJECT_FILE).id == "p"


def test_a_missing_project_is_reported_by_path(tmp_path):
    with pytest.raises(FileNotFoundError, match="no project file"):
        load_project(tmp_path / "absent")


# -- schema version --------------------------------------------------------


def test_a_newer_schema_is_refused_rather_than_guessed(tmp_path):
    """A newer file may hold fields whose meaning this build does not know."""
    path = save_project(simple(), tmp_path / "proj")
    raw = json.loads(path.read_text())
    raw["schema_version"] = SCHEMA_VERSION + 1
    path.write_text(json.dumps(raw))
    with pytest.raises(SchemaVersionError, match="Upgrade planreplan"):
        load_project(tmp_path / "proj")


def test_an_older_schema_reports_the_missing_migration(tmp_path):
    path = save_project(simple(), tmp_path / "proj")
    raw = json.loads(path.read_text())
    raw["schema_version"] = 0
    path.write_text(json.dumps(raw))
    with pytest.raises(SchemaVersionError, match="no migration"):
        load_project(tmp_path / "proj")


def test_saving_stamps_the_current_version_over_a_stale_one(tmp_path):
    stored = load_project(save_project(simple(schema_version=99), tmp_path / "proj").parent)
    assert stored.schema_version == SCHEMA_VERSION


# -- the timezone that an ISO offset cannot carry --------------------------


def test_the_iana_zone_name_survives_a_round_trip(tmp_path):
    """An ISO offset cannot say whether a zone observes daylight saving.

    Reloading from ``-05:00`` alone silently moves every local shift boundary
    after the next transition by an hour, so the name is part of the file.
    """
    original = simple(axis=TimeAxis(epoch=datetime(2026, 3, 1, tzinfo=NY)))
    save_project(original, tmp_path / "proj")
    reloaded = load_project(tmp_path / "proj")
    assert str(reloaded.axis.epoch.tzinfo) == "America/New_York"
    assert reloaded.axis.to_datetime(200) == original.axis.to_datetime(200)


def test_a_reloaded_axis_still_absorbs_a_dst_transition(tmp_path):
    """The failure this guards: same tick, different wall-clock, after a reload."""
    axis = TimeAxis(epoch=datetime(2026, 3, 1, tzinfo=NY))
    save_project(simple(axis=axis), tmp_path / "proj")
    reloaded = load_project(tmp_path / "proj").axis
    across = axis.to_tick_exact(datetime(2026, 3, 9, tzinfo=NY))
    assert reloaded.to_datetime(across) == axis.to_datetime(across)
    assert reloaded.to_datetime(across).utcoffset() != axis.to_datetime(0).utcoffset()


def test_exceptions_and_dates_round_trip(tmp_path):
    holiday = CalendarException(
        start_date=date(2026, 9, 9),
        end_date=date(2026, 9, 9),
        holiday=True,
        reason="Labour Day",
    )
    calendar = Calendar(id="5x8", week_pattern=dict.fromkeys(WEEKDAYS, DAY), exceptions=(holiday,))
    original = simple(calendars=(calendar,))
    save_project(original, tmp_path / "proj")
    reloaded = load_project(tmp_path / "proj")
    assert reloaded.calendars[0].exceptions[0].holiday is True
    assert reloaded.calendars[0].exceptions[0].start_date == date(2026, 9, 9)
    assert reloaded == original


# -- generated projects ----------------------------------------------------


@given(project=projects())
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_generated_projects_round_trip(project, tmp_path_factory):
    """Generated projects reach corners hand-written fixtures do not."""
    directory = tmp_path_factory.mktemp("proj")
    save_project(project, directory)
    assert load_project(directory) == project


@given(project=projects())
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_generated_projects_are_valid_by_construction(project):
    """A generator that mostly produced rejects would test the validator, not the code."""
    index = validate_project(project)
    assert index.leaves


@given(project=projects())
@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_validity_survives_persistence(project, tmp_path_factory):
    directory = tmp_path_factory.mktemp("proj")
    save_project(project, directory)
    before = validate_project(project)
    after = validate_project(load_project(directory))
    assert after.leaves == before.leaves
    assert after.leaf_dependencies == before.leaf_dependencies
    assert after.horizon == before.horizon
