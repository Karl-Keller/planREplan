"""Schedules: full coverage, endpoint pinning, content addressing, persistence."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from hypothesis import HealthCheck, given, settings
from pydantic import ValidationError

from planreplan.cpm import analyse
from planreplan.domain import (
    Baseline,
    Calendar,
    DayOfWeek,
    EntryState,
    Project,
    Schedule,
    ScheduleEntry,
    Shift,
    Task,
    TimeAxis,
    validate_project,
)
from planreplan.io import list_schedules, load_schedule, save_project, save_schedule
from strategies import projects

NY = ZoneInfo("America/New_York")
AXIS = TimeAxis(epoch=datetime(2026, 9, 7, tzinfo=NY))
CAL = Calendar(
    id="5x8",
    week_pattern=dict.fromkeys(
        tuple(DayOfWeek)[:5], (Shift(name="day", start_minute=480, end_minute=960),)
    ),
)


def entry(task_id="a", start=8, finish=16, **kwargs) -> ScheduleEntry:
    return ScheduleEntry(task_id=task_id, start=start, finish=finish, **kwargs)


def schedule(*entries: ScheduleEntry, **kwargs) -> Schedule:
    return Schedule(entries={e.task_id: e for e in entries}, **kwargs)


# -- entry rules -----------------------------------------------------------


def test_an_entry_cannot_finish_before_it_starts():
    with pytest.raises(ValidationError, match="before its start"):
        entry(start=32, finish=8)


def test_resume_must_fall_inside_the_span():
    with pytest.raises(ValidationError, match="outside"):
        entry(start=8, finish=16, resume_at=40)


def test_a_planned_entry_resumes_where_it_starts():
    assert entry().effective_resume == 8


def test_pinning_is_per_endpoint_not_per_task():
    """PLANNED is freely movable; the other two have a start that is a fact."""
    assert not entry(state=EntryState.PLANNED).is_pinned_start
    assert entry(state=EntryState.IN_PROGRESS, resume_at=12).is_pinned_start
    assert entry(state=EntryState.COMPLETE).is_pinned_start


def test_an_in_progress_entry_carries_a_pinned_start_and_a_movable_remainder():
    """The Friday-afternoon case: begun Friday, remainder re-planned to Monday."""
    started = entry(start=109, finish=181, state=EntryState.IN_PROGRESS, resume_at=176)
    assert started.is_pinned_start
    assert started.effective_resume == 176
    assert started.start == 109


# -- content addressing ----------------------------------------------------


def test_identical_schedules_share_an_id():
    """A UUID would have broken determinism for no gain."""
    assert schedule(entry()).id == schedule(entry()).id


def test_a_moved_task_changes_the_id():
    assert schedule(entry()).id != schedule(entry(start=32, finish=40)).id


def test_solver_metadata_does_not_change_the_identity_of_a_plan():
    """Two solvers reaching the same plan reached the same plan."""
    plain = schedule(entry())
    annotated = schedule(entry(), solver_info={"status": "OPTIMAL", "wall_time": 3})
    assert plain.id == annotated.id


def test_spans_expose_the_shape_aggregation_consumes():
    assert schedule(entry(), entry("b", 32, 40)).spans() == {"a": (8, 16), "b": (32, 40)}


# -- persistence -----------------------------------------------------------


def _project() -> Project:
    return Project(
        id="p",
        axis=AXIS,
        calendars=(CAL,),
        default_calendar_id="5x8",
        tasks=(Task(id="a", duration=8),),
    )


def test_a_schedule_round_trips_through_its_own_id(tmp_path):
    save_project(_project(), tmp_path / "p")
    original = schedule(entry(), project_finish=16)
    save_schedule(original, tmp_path / "p")
    assert load_schedule(tmp_path / "p", original.id) == original


def test_saving_the_same_schedule_twice_is_idempotent(tmp_path):
    save_project(_project(), tmp_path / "p")
    stored = schedule(entry())
    save_schedule(stored, tmp_path / "p")
    save_schedule(stored, tmp_path / "p")
    assert list_schedules(tmp_path / "p") == (stored.id,)


def test_an_edited_schedule_file_is_refused(tmp_path):
    """Content addressing means a tampered plan cannot pose as the original.

    The edit is deliberately one that stays structurally valid — a task moved
    to another working day — since a malformed file is already caught by
    parsing. The digest is what catches a plausible lie.
    """
    save_project(_project(), tmp_path / "p")
    stored = schedule(entry(), project_finish=16)
    path = save_schedule(stored, tmp_path / "p")
    path.write_text(
        path.read_text()
        .replace('"start": 8', '"start": 32')
        .replace('"finish": 16', '"finish": 40')
    )
    with pytest.raises(ValueError, match="no longer the plan it claims"):
        load_schedule(tmp_path / "p", stored.id)


def test_a_missing_schedule_is_reported(tmp_path):
    save_project(_project(), tmp_path / "p")
    with pytest.raises(FileNotFoundError, match="no schedule"):
        load_schedule(tmp_path / "p", "deadbeef")


def test_listing_an_empty_project_yields_nothing(tmp_path):
    save_project(_project(), tmp_path / "p")
    assert list_schedules(tmp_path / "p") == ()


def test_a_baseline_is_a_named_frozen_schedule(tmp_path):
    baseline = Baseline(
        name="kickoff", frozen_at=datetime(2026, 9, 7, tzinfo=UTC), schedule=schedule(entry())
    )
    project = _project().model_copy(update={"baselines": (baseline,)})
    save_project(project, tmp_path / "p")
    from planreplan.io import load_project

    reloaded = load_project(tmp_path / "p")
    assert reloaded.baselines[0].name == "kickoff"
    assert reloaded.baselines[0].schedule.id == baseline.schedule.id


# -- CPM produces one -------------------------------------------------------


@given(project=projects())
@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_cpm_yields_a_schedule_covering_every_leaf(project):
    """Full coverage is what makes churn and variance computable at all."""
    index = validate_project(project)
    result = analyse(index)
    plan = result.to_schedule()
    assert set(plan.entries) == set(index.leaves)
    assert plan.project_finish == result.project_finish
    assert all(e.state is EntryState.PLANNED for e in plan.entries.values())


@given(project=projects())
@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_the_cpm_schedule_is_deterministic(project):
    index = validate_project(project)
    assert analyse(index).to_schedule().id == analyse(index).to_schedule().id
