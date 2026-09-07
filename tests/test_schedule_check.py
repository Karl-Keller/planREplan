"""Schedule verification: every rule, reported all at once, without a solver."""

from datetime import datetime
from zoneinfo import ZoneInfo

from hypothesis import HealthCheck, given, settings

from planreplan.cpm import analyse
from planreplan.domain import (
    Assignment,
    Calendar,
    DayOfWeek,
    Dependency,
    DependencyKind,
    EntryState,
    Project,
    Resource,
    Schedule,
    ScheduleEntry,
    Shift,
    Task,
    TimeAxis,
    ViolationKind,
    check_schedule,
    validate_project,
)
from planreplan.solve import CpSatScheduler, SolveOptions, greedy_schedule
from strategies import projects

NY = ZoneInfo("America/New_York")
AXIS = TimeAxis(epoch=datetime(2026, 9, 7, tzinfo=NY))
CAL = Calendar(
    id="5x8",
    week_pattern=dict.fromkeys(
        tuple(DayOfWeek)[:5], (Shift(name="day", start_minute=480, end_minute=960),)
    ),
)


def build(tasks, deps=(), resources=(), assignments=()) -> Project:
    return Project(
        id="x",
        axis=AXIS,
        calendars=(CAL,),
        default_calendar_id="5x8",
        tasks=tuple(tasks),
        dependencies=tuple(deps),
        resources=tuple(resources),
        assignments=tuple(assignments),
    )


def schedule_of(spans, **kwargs) -> Schedule:
    return Schedule(
        entries={
            task_id: ScheduleEntry(task_id=task_id, start=s, finish=f, **kwargs.pop(task_id, {}))
            for task_id, (s, f) in spans.items()
        },
        project_finish=max((f for _, f in spans.values()), default=0),
        **kwargs,
    )


def kinds(violations) -> set[ViolationKind]:
    return {v.kind for v in violations}


# -- a correct schedule ----------------------------------------------------


def test_a_solved_schedule_passes():
    project = build(
        [Task(id="a", duration=8), Task(id="b", duration=8)],
        [Dependency(predecessor_id="a", successor_id="b")],
        [Resource(id="crew", capacity=1)],
        [Assignment(task_id="a", resource_id="crew"), Assignment(task_id="b", resource_id="crew")],
    )
    index = validate_project(project)
    assert check_schedule(index, CpSatScheduler(SolveOptions()).solve(index).schedule) == ()


def test_the_cpm_schedule_passes_when_nothing_contends():
    project = build([Task(id="a", duration=8), Task(id="b", duration=8)])
    index = validate_project(project)
    assert check_schedule(index, analyse(index).to_schedule()) == ()


# -- each rule -------------------------------------------------------------


def test_a_missing_task_is_reported():
    index = validate_project(build([Task(id="a", duration=8), Task(id="b", duration=8)]))
    assert ViolationKind.MISSING_TASK in kinds(check_schedule(index, schedule_of({"a": (8, 16)})))


def test_a_task_that_is_not_in_the_project_is_reported():
    index = validate_project(build([Task(id="a", duration=8)]))
    violations = check_schedule(index, schedule_of({"a": (8, 16), "ghost": (8, 16)}))
    assert ViolationKind.UNKNOWN_TASK in kinds(violations)


def test_starting_when_nobody_works_is_reported():
    index = validate_project(build([Task(id="a", duration=8)]))
    saturday = 5 * 24 + 9
    violations = check_schedule(index, schedule_of({"a": (saturday, saturday + 8)}))
    assert ViolationKind.NON_WORKING_START in kinds(violations)


def test_a_span_holding_the_wrong_amount_of_work_is_reported():
    """The check is work content, not elapsed length: a weekend is not work."""
    index = validate_project(build([Task(id="a", duration=8)]))
    assert ViolationKind.WRONG_WORK_CONTENT in kinds(
        check_schedule(index, schedule_of({"a": (8, 12)}))
    )


def test_a_precedence_violation_is_reported_with_both_tasks():
    project = build(
        [Task(id="a", duration=8), Task(id="b", duration=8)],
        [Dependency(predecessor_id="a", successor_id="b")],
    )
    index = validate_project(project)
    violations = check_schedule(index, schedule_of({"a": (32, 40), "b": (8, 16)}))
    precedence = next(v for v in violations if v.kind is ViolationKind.PRECEDENCE)
    assert set(precedence.task_ids) == {"a", "b"}


def test_a_deliberately_introduced_overlap_is_rejected():
    """The roadmap's acceptance case for the checker."""
    project = build(
        [Task(id="a", duration=8), Task(id="b", duration=8)],
        resources=[Resource(id="crew", capacity=1)],
        assignments=[
            Assignment(task_id="a", resource_id="crew"),
            Assignment(task_id="b", resource_id="crew"),
        ],
    )
    index = validate_project(project)
    overlapped = schedule_of({"a": (8, 16), "b": (8, 16)})
    assert ViolationKind.RESOURCE_OVERLOAD in kinds(check_schedule(index, overlapped))


def test_capacity_that_is_exactly_met_is_not_a_violation():
    project = build(
        [Task(id="a", duration=8), Task(id="b", duration=8)],
        resources=[Resource(id="crew", capacity=2)],
        assignments=[
            Assignment(task_id="a", resource_id="crew"),
            Assignment(task_id="b", resource_id="crew"),
        ],
    )
    index = validate_project(project)
    assert check_schedule(index, schedule_of({"a": (8, 16), "b": (8, 16)})) == ()


def test_unstarted_work_may_not_begin_before_the_data_date():
    index = validate_project(build([Task(id="a", duration=8)]))
    plan = Schedule(
        data_date=200,
        project_finish=16,
        entries={"a": ScheduleEntry(task_id="a", start=8, finish=16)},
    )
    assert ViolationKind.DATA_DATE in kinds(check_schedule(index, plan))


def test_completed_work_may_not_finish_after_the_data_date():
    index = validate_project(build([Task(id="a", duration=8)]))
    plan = Schedule(
        data_date=8,
        project_finish=16,
        entries={"a": ScheduleEntry(task_id="a", start=8, finish=16, state=EntryState.COMPLETE)},
    )
    assert ViolationKind.DATA_DATE in kinds(check_schedule(index, plan))


def test_every_violation_is_reported_at_once():
    """Three problems should be said once, not discovered three times."""
    project = build(
        [Task(id="a", duration=8), Task(id="b", duration=8)],
        [Dependency(predecessor_id="a", successor_id="b")],
    )
    index = validate_project(project)
    violations = check_schedule(index, schedule_of({"a": (32, 36), "b": (8, 16)}))
    assert len(violations) >= 2
    assert {ViolationKind.WRONG_WORK_CONTENT, ViolationKind.PRECEDENCE} <= kinds(violations)


# -- invariants ------------------------------------------------------------

GENERATED = projects(kinds=tuple(DependencyKind), allow_summaries=False, max_tasks=6)
SETTINGS = settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@given(project=GENERATED)
@SETTINGS
def test_what_the_solver_produces_the_checker_accepts(project):
    """The two agree, or one of them is wrong about the same rules."""
    index = validate_project(project)
    schedule = CpSatScheduler(SolveOptions()).solve(index).schedule
    assert check_schedule(index, schedule) == ()


@given(project=GENERATED)
@SETTINGS
def test_the_greedy_schedule_also_passes(project):
    """The fallback is a real plan, not a placeholder."""
    index = validate_project(project)
    assert check_schedule(index, greedy_schedule(index)) == ()


@given(project=GENERATED)
@SETTINGS
def test_checking_needs_no_solver_state(project):
    index = validate_project(project)
    schedule = greedy_schedule(index)
    assert check_schedule(index, schedule) == check_schedule(index, schedule)
