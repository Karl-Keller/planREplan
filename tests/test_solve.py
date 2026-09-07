"""CP-SAT scheduling: acceptance cases, determinism, and honest status reporting."""

from datetime import datetime
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest
from hypothesis import HealthCheck, given, settings

from planreplan.cpm import analyse
from planreplan.domain import (
    Assignment,
    Calendar,
    DayOfWeek,
    Dependency,
    DependencyKind,
    Project,
    Resource,
    Shift,
    Task,
    TimeAxis,
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
FAST = SolveOptions(time_limit_seconds=10)


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


def task(identifier: str, duration: int, **kwargs) -> Task:
    return Task(id=identifier, duration=duration, **kwargs)


def link(a: str, b: str, kind: str = "FS", lag: int = 0) -> Dependency:
    return Dependency(predecessor_id=a, successor_id=b, kind=DependencyKind(kind), lag=lag)


def solve(project: Project, options: SolveOptions = FAST):
    return CpSatScheduler(options).solve(validate_project(project))


# -- the roadmap's acceptance cases ----------------------------------------


def test_without_resources_the_solver_matches_cpm():
    """Resources are the only thing that can push a task past its early date."""
    project = build([task("a", 8), task("b", 8), task("c", 8)], [link("a", "b"), link("b", "c")])
    index = validate_project(project)
    assert solve(project).schedule.project_finish == analyse(index).project_finish


def test_a_capacity_one_crew_serialises_two_parallel_tasks():
    project = build(
        [task("a", 8), task("b", 8)],
        resources=[Resource(id="crew", capacity=1)],
        assignments=[
            Assignment(task_id="a", resource_id="crew"),
            Assignment(task_id="b", resource_id="crew"),
        ],
    )
    result = solve(project)
    a, b = result.schedule.entries["a"], result.schedule.entries["b"]
    assert a.finish <= b.start or b.finish <= a.start
    assert result.schedule.project_finish == 40  # two working days, not one


def test_capacity_admits_as_much_parallelism_as_it_has():
    """Four tasks, a crew of two: two days, not four."""
    project = build(
        [task(f"t{n}", 8) for n in range(4)],
        resources=[Resource(id="crew", capacity=2)],
        assignments=[Assignment(task_id=f"t{n}", resource_id="crew") for n in range(4)],
    )
    assert solve(project).schedule.project_finish == 40


def test_the_same_seed_gives_the_same_schedule():
    """NFR1. Fixed seed and single worker: parallel workers race."""
    project = build(
        [task(f"t{n}", 8) for n in range(6)],
        [link("t0", "t1"), link("t2", "t3")],
        resources=[Resource(id="crew", capacity=2)],
        assignments=[Assignment(task_id=f"t{n}", resource_id="crew") for n in range(6)],
    )
    first = solve(project, SolveOptions(seed=7, workers=1))
    second = solve(project, SolveOptions(seed=7, workers=1))
    assert first.schedule.id == second.schedule.id


def test_the_solver_respects_calendars():
    project = build([task("a", 16)])
    entry = solve(project).schedule.entries["a"]
    calendar = validate_project(project).effective_calendar("a")
    assert calendar.is_working(entry.start)
    assert calendar.working_prefix(entry.finish) - calendar.working_prefix(entry.start) == 16


def test_the_solver_respects_an_elapsed_lag():
    project = build([task("a", 8), task("b", 8)], [link("a", "b", lag=48)])
    schedule = solve(project).schedule
    assert schedule.entries["b"].start >= schedule.entries["a"].finish + 48


# -- greedy fallback -------------------------------------------------------


def test_the_greedy_schedule_is_feasible_on_its_own():
    project = build(
        [task(f"t{n}", 8) for n in range(4)],
        resources=[Resource(id="crew", capacity=1)],
        assignments=[Assignment(task_id=f"t{n}", resource_id="crew") for n in range(4)],
    )
    schedule = greedy_schedule(validate_project(project))
    spans = sorted(schedule.spans().values())
    assert all(a[1] <= b[0] for a, b in pairwise(spans))


def test_greedy_never_beats_the_optimum():
    project = build(
        [task(f"t{n}", 8) for n in range(5)],
        resources=[Resource(id="crew", capacity=2)],
        assignments=[Assignment(task_id=f"t{n}", resource_id="crew") for n in range(5)],
    )
    index = validate_project(project)
    assert greedy_schedule(index).project_finish >= solve(project).schedule.project_finish


def test_an_exhausted_time_limit_returns_a_real_schedule_not_a_shrug():
    """UNKNOWN means the clock ran out, never that nothing exists.

    Claiming infeasibility the solver did not prove is exactly the
    confident-wrong answer this system is built to avoid.
    """
    project = build(
        [task(f"t{n}", 8) for n in range(30)],
        resources=[Resource(id="crew", capacity=3)],
        assignments=[Assignment(task_id=f"t{n}", resource_id="crew") for n in range(30)],
    )
    result = CpSatScheduler(SolveOptions(time_limit_seconds=0.001)).solve(validate_project(project))
    assert result.schedule.entries  # a plan, whatever the status
    assert len(result.schedule.entries) == 30


# -- invariants over generated projects ------------------------------------

GENERATED = projects(kinds=tuple(DependencyKind), allow_summaries=False, max_tasks=6)
SETTINGS = settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@given(project=GENERATED)
@SETTINGS
def test_solved_schedules_cover_every_leaf_and_land_on_working_ticks(project):
    index = validate_project(project)
    schedule = CpSatScheduler(FAST).solve(index).schedule
    assert set(schedule.entries) == set(index.leaves)
    for task_id, entry in schedule.entries.items():
        calendar = index.effective_calendar(task_id)
        duration = index.task(task_id).duration
        if duration:
            assert calendar.is_working(entry.start)
        worked = calendar.working_prefix(entry.finish) - calendar.working_prefix(entry.start)
        assert worked == duration


@given(project=GENERATED)
@SETTINGS
def test_solved_schedules_satisfy_every_link(project):
    index = validate_project(project)
    entries = CpSatScheduler(FAST).solve(index).schedule.entries
    for dependency in index.leaf_dependencies:
        predecessor, successor = (
            entries[dependency.predecessor_id],
            entries[dependency.successor_id],
        )
        if dependency.kind is DependencyKind.FS:
            assert successor.start >= predecessor.finish + dependency.lag
        elif dependency.kind is DependencyKind.SS:
            assert successor.start >= predecessor.start + dependency.lag
        elif dependency.kind is DependencyKind.FF:
            assert successor.finish >= predecessor.finish + dependency.lag
        else:
            assert successor.finish >= predecessor.start + dependency.lag


@given(project=GENERATED)
@SETTINGS
def test_a_solved_makespan_never_beats_the_resource_free_bound(project):
    """CPM ignores capacities, so it is an optimistic bound the solver cannot pass."""
    index = validate_project(project)
    assert (
        CpSatScheduler(FAST).solve(index).schedule.project_finish >= analyse(index).project_finish
    )


@given(project=GENERATED)
@SETTINGS
def test_resource_capacity_is_never_exceeded(project):
    index = validate_project(project)
    entries = CpSatScheduler(FAST).solve(index).schedule.entries
    for resource in index.project.resources:
        reservations = [
            (entries[a.task_id].start, entries[a.task_id].finish, a.demand)
            for task_id in index.leaves
            for a in index.assignments_for(task_id)
            if a.resource_id == resource.id
        ]
        for moment, _, _ in reservations:
            concurrent = sum(d for s, e, d in reservations if s <= moment < e)
            assert concurrent <= resource.capacity


@given(project=GENERATED)
@SETTINGS
def test_solving_is_deterministic(project):
    index = validate_project(project)
    scheduler = CpSatScheduler(SolveOptions(seed=3, workers=1, time_limit_seconds=10))
    assert scheduler.solve(index).schedule.id == scheduler.solve(index).schedule.id


@pytest.mark.parametrize("workers", [1, 2])
def test_worker_count_is_configurable(workers):
    project = build([task("a", 8)])
    assert solve(project, SolveOptions(workers=workers)).schedule.entries["a"].start == 8
