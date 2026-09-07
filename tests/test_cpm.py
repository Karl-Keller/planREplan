"""CPM over the tick axis: precedence algebra, floats, and the hand-checked fixture."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from hypothesis import HealthCheck, given, settings

from planreplan.cpm import CpmError, analyse, forward_pass, topological_order
from planreplan.domain import (
    Calendar,
    DayOfWeek,
    Dependency,
    DependencyKind,
    Project,
    Shift,
    Task,
    TimeAxis,
    validate_project,
)
from strategies import projects

NY = ZoneInfo("America/New_York")
AXIS = TimeAxis(epoch=datetime(2026, 9, 7, tzinfo=NY))  # a Monday, midnight
CAL = Calendar(
    id="5x8",
    week_pattern=dict.fromkeys(
        tuple(DayOfWeek)[:5], (Shift(name="day", start_minute=8 * 60, end_minute=16 * 60),)
    ),
)


def build(tasks, deps) -> Project:
    return Project(
        id="x",
        axis=AXIS,
        calendars=(CAL,),
        default_calendar_id="5x8",
        tasks=tuple(tasks),
        dependencies=tuple(deps),
    )


def run(tasks, deps):
    return analyse(validate_project(build(tasks, deps)))


def task(identifier: str, duration: int, **kwargs) -> Task:
    return Task(id=identifier, duration=duration, **kwargs)


def link(a: str, b: str, kind: str = "FS", lag: int = 0) -> Dependency:
    return Dependency(predecessor_id=a, successor_id=b, kind=DependencyKind(kind), lag=lag)


# -- the precedence algebra ------------------------------------------------


def test_a_finish_start_chain_walks_the_calendar():
    """Three eight-hour tasks occupy three consecutive working days."""
    result = run([task("a", 8), task("b", 8), task("c", 8)], [link("a", "b"), link("b", "c")])
    assert (result.tasks["a"].early_start, result.tasks["a"].early_finish) == (8, 16)
    assert (result.tasks["b"].early_start, result.tasks["b"].early_finish) == (32, 40)
    assert (result.tasks["c"].early_start, result.tasks["c"].early_finish) == (56, 64)
    assert result.project_finish == 64


def test_an_elapsed_lag_resolves_forward_out_of_a_gap():
    """Lag is wall-clock: 24 ticks past Monday 16:00 is Tuesday 16:00, which
    nobody works, so the successor starts Wednesday morning."""
    result = run([task("a", 8), task("b", 8)], [link("a", "b", lag=24)])
    assert result.tasks["a"].early_finish == 16  # Monday 16:00
    assert result.tasks["b"].early_start == 56  # Wednesday 08:00, not Tuesday 16:00


def test_start_start_with_lag_offsets_the_successor_start():
    result = run([task("a", 16), task("b", 8)], [link("a", "b", "SS", lag=8)])
    assert result.tasks["b"].early_start == 32


def test_finish_finish_aligns_the_finishes():
    """FF bounds the successor's finish, so the engine solves back for a start."""
    result = run([task("a", 8), task("b", 8)], [link("a", "b", "FF")])
    assert result.tasks["a"].early_finish == result.tasks["b"].early_finish == 16


def test_start_finish_bounds_the_successor_finish_by_the_predecessor_start():
    result = run([task("a", 8), task("b", 8)], [link("a", "b", "SF")])
    assert result.tasks["b"].early_finish >= result.tasks["a"].early_start


def test_a_parallel_task_carries_float_in_working_ticks():
    """Sixteen ticks of float is two working days, not sixteen elapsed hours."""
    result = run(
        [task("a", 8), task("b", 8), task("c", 8), task("d", 8)],
        [link("a", "b"), link("b", "c")],
    )
    assert result.tasks["d"].total_float == 16
    assert result.tasks["d"].free_float == 16
    assert result.critical_path == ("a", "b", "c")


def test_a_milestone_has_no_span():
    result = run([task("a", 8), task("m", 0, is_milestone=True)], [link("a", "m")])
    milestone = result.tasks["m"]
    assert milestone.early_start == milestone.early_finish


def test_a_deadline_earlier_than_the_early_finish_yields_negative_float():
    """Negative float is the honest report of a deadline that cannot be met."""
    result = run([task("a", 24, deadline=16)], [])
    assert result.tasks["a"].total_float < 0
    assert result.tasks["a"].is_critical


def test_a_cycle_reaching_the_engine_fails_loudly():
    with pytest.raises(CpmError, match="cycle"):
        topological_order(["a", "b"], [link("a", "b"), link("b", "a")])


# -- the hand-checked fixture ----------------------------------------------

#: A twelve-task foundation package on a five-day eight-hour calendar, epoch
#: Monday 2026-09-07. Dates verified by hand against the calendar: excavate
#: runs Tue-Thu, the 48-tick cure lag after `pour` lands on Wednesday 16:00 and
#: resolves forward to Thursday morning, and the project completes Tuesday
#: 2026-09-29 at 08:00. `site_utilities` carries twelve working days of float —
#: the gap the Phase 3 repair scenario exploits.
FIXTURE_TASKS = [
    task("mobilize", 8),
    task("survey", 8),
    task("excavate", 24),
    task("form_footings", 16),
    task("rebar", 16),
    task("inspection", 8),
    task("pour", 16),
    task("strip_forms", 8),
    task("backfill", 16),
    task("site_utilities", 24),
    task("erosion_control", 8),
    task("foundation_complete", 0, is_milestone=True),
]
FIXTURE_LINKS = [
    link("mobilize", "survey"),
    link("mobilize", "excavate"),
    link("mobilize", "site_utilities"),
    link("mobilize", "erosion_control"),
    link("survey", "form_footings"),
    link("excavate", "form_footings"),
    link("form_footings", "rebar"),
    link("rebar", "inspection"),
    link("inspection", "pour"),
    link("pour", "strip_forms", lag=48),  # two elapsed days of cure
    link("strip_forms", "backfill"),
    link("backfill", "foundation_complete"),
    link("site_utilities", "foundation_complete"),
    link("erosion_control", "foundation_complete"),
]
#: task -> (early_start, early_finish, late_start, late_finish, total, free)
FIXTURE_EXPECTED = {
    "mobilize": (8, 16, 8, 16, 0, 0),
    "survey": (32, 40, 80, 88, 16, 16),
    "excavate": (32, 88, 32, 88, 0, 0),
    "form_footings": (104, 184, 104, 184, 0, 0),
    "rebar": (200, 232, 200, 232, 0, 0),
    "inspection": (248, 256, 248, 256, 0, 0),
    "pour": (272, 352, 272, 352, 0, 0),
    "strip_forms": (416, 424, 416, 424, 0, 0),
    "backfill": (440, 520, 440, 520, 0, 0),
    "site_utilities": (32, 88, 416, 520, 96, 96),
    "erosion_control": (32, 40, 512, 520, 112, 112),
    "foundation_complete": (536, 536, 536, 536, 0, 0),
}


@pytest.fixture
def foundation():
    return run(FIXTURE_TASKS, FIXTURE_LINKS)


@pytest.mark.parametrize("task_id", list(FIXTURE_EXPECTED))
def test_fixture_dates_and_floats(foundation, task_id):
    entry = foundation.tasks[task_id]
    actual = (
        entry.early_start,
        entry.early_finish,
        entry.late_start,
        entry.late_finish,
        entry.total_float,
        entry.free_float,
    )
    assert actual == FIXTURE_EXPECTED[task_id]


def test_fixture_completes_on_the_expected_morning(foundation):
    assert foundation.project_finish == 536
    assert AXIS.to_datetime(536) == datetime(2026, 9, 29, 8, tzinfo=NY)


def test_fixture_critical_path_runs_through_the_inspection(foundation):
    assert foundation.critical_path == (
        "mobilize",
        "excavate",
        "form_footings",
        "rebar",
        "inspection",
        "pour",
        "strip_forms",
        "backfill",
        "foundation_complete",
    )


def test_fixture_survey_can_slide_two_working_days(foundation):
    """Float in working ticks: Tuesday to Thursday, not sixteen elapsed hours."""
    assert foundation.tasks["survey"].total_float == 16
    assert AXIS.to_datetime(foundation.tasks["survey"].late_start) == datetime(
        2026, 9, 10, 8, tzinfo=NY
    )


# -- invariants ------------------------------------------------------------

GENERATED = projects(kinds=tuple(DependencyKind), allow_summaries=False)
SETTINGS = settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])


def _satisfies(kind: DependencyKind, pred, succ, lag: int) -> bool:
    if kind is DependencyKind.FS:
        return succ.early_start >= pred.early_finish + lag
    if kind is DependencyKind.SS:
        return succ.early_start >= pred.early_start + lag
    if kind is DependencyKind.FF:
        return succ.early_finish >= pred.early_finish + lag
    return succ.early_finish >= pred.early_start + lag


@given(project=GENERATED)
@SETTINGS
def test_the_forward_pass_satisfies_every_link(project):
    """The strongest statement available: all four kinds, with lags, hold."""
    index = validate_project(project)
    result = analyse(index)
    for dependency in index.leaf_dependencies:
        assert _satisfies(
            dependency.kind,
            result.tasks[dependency.predecessor_id],
            result.tasks[dependency.successor_id],
            dependency.lag,
        )


@given(project=GENERATED)
@SETTINGS
def test_total_float_is_at_least_free_float_and_neither_is_negative(project):
    for entry in analyse(validate_project(project)).tasks.values():
        assert entry.total_float >= entry.free_float >= 0


@given(project=GENERATED)
@SETTINGS
def test_zero_total_float_is_exactly_the_critical_path(project):
    result = analyse(validate_project(project))
    zero = {t.task_id for t in result.tasks.values() if t.total_float == 0}
    assert zero == set(result.critical_path)


@given(project=GENERATED)
@SETTINGS
def test_early_dates_land_on_working_ticks_and_span_the_duration(project):
    index = validate_project(project)
    for entry in analyse(index).tasks.values():
        calendar = index.effective_calendar(entry.task_id)
        duration = index.task(entry.task_id).duration
        assert calendar.is_working(entry.early_start)
        worked = calendar.working_prefix(entry.early_finish) - calendar.working_prefix(
            entry.early_start
        )
        assert worked == duration


@given(project=GENERATED)
@SETTINGS
def test_late_dates_never_precede_early_dates(project):
    for entry in analyse(validate_project(project)).tasks.values():
        assert entry.late_start >= entry.early_start
        assert entry.late_finish >= entry.early_finish


@given(project=GENERATED)
@SETTINGS
def test_the_project_finish_is_the_latest_early_finish(project):
    result = analyse(validate_project(project))
    assert result.project_finish == max(t.early_finish for t in result.tasks.values())


@given(project=GENERATED)
@SETTINGS
def test_analysis_is_deterministic(project):
    """NFR1: identical inputs, identical outputs."""
    index = validate_project(project)
    assert analyse(index) == analyse(index)


@given(project=GENERATED)
@SETTINGS
def test_a_data_date_pushes_every_start_forward(project):
    index = validate_project(project)
    baseline = analyse(index)
    later = analyse(index, data_date=index.horizon // 4)
    for task_id, entry in later.tasks.items():
        assert entry.early_start >= baseline.tasks[task_id].early_start


@given(project=GENERATED)
@SETTINGS
def test_every_project_with_tasks_has_a_critical_path(project):
    result = analyse(validate_project(project))
    assert result.critical_path


def test_the_forward_pass_is_usable_on_its_own():
    early = forward_pass(validate_project(build([task("a", 8)], [])))
    assert early["a"] == (8, 16)
