"""Whole-project validation: references, WBS shape, expansion, effective calendars."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from planreplan.domain import (
    Assignment,
    Calendar,
    CalendarException,
    DayOfWeek,
    Dependency,
    DependencyKind,
    PayRules,
    Project,
    ProjectValidationError,
    Resource,
    Shift,
    Task,
    TimeAxis,
    validate_project,
)

NY = ZoneInfo("America/New_York")
AXIS = TimeAxis(epoch=datetime(2026, 9, 7, tzinfo=NY))  # a Monday, midnight

DAY = (Shift(name="day", start_minute=8 * 60, end_minute=16 * 60),)
WEEKDAYS = tuple(DayOfWeek)[:5]
FIVE_BY_EIGHT = Calendar(id="5x8", week_pattern=dict.fromkeys(WEEKDAYS, DAY))
SEVEN_BY_EIGHT = Calendar(id="7x8", week_pattern=dict.fromkeys(DayOfWeek, DAY))
NIGHTS = Calendar(
    id="nights", week_pattern=dict.fromkeys(DayOfWeek, (Shift(start_minute=0, end_minute=8 * 60),))
)


def make(**overrides) -> Project:
    base = {
        "id": "p",
        "axis": AXIS,
        "calendars": (FIVE_BY_EIGHT, SEVEN_BY_EIGHT, NIGHTS),
        "default_calendar_id": "5x8",
        "tasks": (Task(id="a", duration=8), Task(id="b", duration=8)),
        "dependencies": (Dependency(predecessor_id="a", successor_id="b"),),
    }
    return Project(**{**base, **overrides})


def problems_of(project: Project) -> tuple[str, ...]:
    with pytest.raises(ProjectValidationError) as caught:
        validate_project(project)
    return caught.value.problems


# -- references and uniqueness ---------------------------------------------


def test_a_well_formed_project_validates():
    index = validate_project(make())
    assert index.leaves == ("a", "b")


def test_duplicate_task_ids_are_rejected():
    project = make(tasks=(Task(id="a", duration=8), Task(id="a", duration=8)))
    assert any("duplicate task id" in p for p in problems_of(project))


def test_unknown_default_calendar_is_rejected():
    assert any("default_calendar_id" in p for p in problems_of(make(default_calendar_id="nope")))


def test_unknown_references_are_named():
    project = make(
        tasks=(Task(id="a", duration=8, calendar_id="ghost"),),
        dependencies=(Dependency(predecessor_id="a", successor_id="missing"),),
    )
    reported = problems_of(project)
    assert any("unknown calendar 'ghost'" in p for p in reported)
    assert any("successor 'missing'" in p for p in reported)


def test_every_problem_is_reported_in_one_pass():
    """A scheduler importing a real project wants the list, not whack-a-mole."""
    project = make(
        tasks=(Task(id="a", duration=8, calendar_id="ghost"), Task(id="a", duration=0)),
        default_calendar_id="nope",
    )
    assert len(problems_of(project)) >= 3


# -- WBS shape -------------------------------------------------------------


def test_summary_tasks_carry_no_duration():
    project = make(
        tasks=(Task(id="s", duration=8), Task(id="a", duration=8, parent_id="s")),
        dependencies=(),
    )
    assert any("summary task 's' carries duration" in p for p in problems_of(project))


def test_leaf_tasks_must_have_duration():
    project = make(tasks=(Task(id="a"),), dependencies=())
    assert any("leaf task 'a' has no duration" in p for p in problems_of(project))


def test_a_task_cannot_be_its_own_ancestor():
    project = make(
        tasks=(Task(id="a", parent_id="b"), Task(id="b", parent_id="a")), dependencies=()
    )
    assert any("own ancestor" in p for p in problems_of(project))


def test_wbs_paths_are_derived_from_the_tree_in_declaration_order():
    project = make(
        tasks=(
            Task(id="s"),
            Task(id="a", duration=8, parent_id="s"),
            Task(id="b", duration=8, parent_id="s"),
            Task(id="t"),
            Task(id="c", duration=8, parent_id="t"),
        ),
        dependencies=(),
    )
    index = validate_project(project)
    assert index.wbs_path("s") == "1"
    assert index.wbs_path("a") == "1.1"
    assert index.wbs_path("b") == "1.2"
    assert index.wbs_path("t") == "2"
    assert index.wbs_path("c") == "2.1"
    assert index.leaves == ("a", "b", "c")


# -- assignments -----------------------------------------------------------


def test_demand_may_not_exceed_capacity():
    project = make(
        resources=(Resource(id="crew", capacity=2),),
        assignments=(Assignment(task_id="a", resource_id="crew", demand=3),),
    )
    assert any("capacity 2" in p for p in problems_of(project))


def test_only_leaves_carry_assignments():
    project = make(
        tasks=(Task(id="s"), Task(id="a", duration=8, parent_id="s")),
        dependencies=(),
        resources=(Resource(id="crew", capacity=2),),
        assignments=(Assignment(task_id="s", resource_id="crew"),),
    )
    assert any("targets summary task" in p for p in problems_of(project))


# -- summary-level dependency expansion ------------------------------------


def summary_project(kind: DependencyKind, *, on_predecessor: bool) -> Project:
    tasks = (
        Task(id="s"),
        Task(id="s1", duration=8, parent_id="s"),
        Task(id="s2", duration=8, parent_id="s"),
        Task(id="leaf", duration=8),
    )
    link = (
        Dependency(predecessor_id="s", successor_id="leaf", kind=kind)
        if on_predecessor
        else Dependency(predecessor_id="leaf", successor_id="s", kind=kind)
    )
    return make(tasks=tasks, dependencies=(link,))


def test_finish_start_expands_over_a_summary_predecessor():
    """B after the summary finishes is B after every leaf finishes: exact."""
    index = validate_project(summary_project(DependencyKind.FS, on_predecessor=True))
    pairs = {(d.predecessor_id, d.successor_id) for d in index.leaf_dependencies}
    assert pairs == {("s1", "leaf"), ("s2", "leaf")}


def test_finish_start_expands_over_a_summary_successor():
    index = validate_project(summary_project(DependencyKind.FS, on_predecessor=False))
    pairs = {(d.predecessor_id, d.successor_id) for d in index.leaf_dependencies}
    assert pairs == {("leaf", "s1"), ("leaf", "s2")}


def test_start_start_from_a_summary_predecessor_is_refused():
    """A summary's start is its earliest leaf; a conjunction would give the latest."""
    reported = problems_of(summary_project(DependencyKind.SS, on_predecessor=True))
    assert any("reads the start of summary task" in p for p in reported)


def test_start_start_into_a_summary_successor_expands():
    """min(starts) >= X is exactly all(starts >= X), so this one is sound."""
    index = validate_project(summary_project(DependencyKind.SS, on_predecessor=False))
    assert len(index.leaf_dependencies) == 2


def test_finish_finish_into_a_summary_successor_is_refused():
    reported = problems_of(summary_project(DependencyKind.FF, on_predecessor=False))
    assert any("constrains the finish of summary task" in p for p in reported)


def test_finish_finish_from_a_summary_predecessor_expands():
    index = validate_project(summary_project(DependencyKind.FF, on_predecessor=True))
    assert len(index.leaf_dependencies) == 2


@pytest.mark.parametrize("on_predecessor", [True, False])
def test_start_finish_over_a_summary_is_refused_on_either_side(on_predecessor):
    """SF reads a start and constrains a finish, so neither side can expand."""
    assert problems_of(summary_project(DependencyKind.SF, on_predecessor=on_predecessor))


def test_leaf_only_dependencies_pass_through_unchanged():
    index = validate_project(make())
    assert index.leaf_dependencies == (Dependency(predecessor_id="a", successor_id="b"),)


# -- cycles ----------------------------------------------------------------


def test_a_dependency_cycle_is_named():
    project = make(
        tasks=(Task(id="a", duration=8), Task(id="b", duration=8), Task(id="c", duration=8)),
        dependencies=(
            Dependency(predecessor_id="a", successor_id="b"),
            Dependency(predecessor_id="b", successor_id="c"),
            Dependency(predecessor_id="c", successor_id="a"),
        ),
    )
    reported = problems_of(project)
    assert any(p.startswith("dependency cycle:") for p in reported)
    assert any("a" in p and "b" in p and "c" in p for p in reported)


def test_a_cycle_created_only_by_expansion_is_still_caught():
    project = make(
        tasks=(
            Task(id="s"),
            Task(id="s1", duration=8, parent_id="s"),
            Task(id="leaf", duration=8),
        ),
        dependencies=(
            Dependency(predecessor_id="s", successor_id="leaf"),
            Dependency(predecessor_id="leaf", successor_id="s1"),
        ),
    )
    assert any("cycle" in p for p in problems_of(project))


# -- effective calendars ---------------------------------------------------


def test_the_effective_calendar_intersects_task_and_resources():
    project = make(
        tasks=(Task(id="a", duration=8, calendar_id="7x8"),),
        dependencies=(),
        resources=(Resource(id="crew", capacity=2, calendar_id="5x8"),),
        assignments=(Assignment(task_id="a", resource_id="crew"),),
    )
    index = validate_project(project)
    effective = index.effective_calendar("a")
    weekend = AXIS.to_tick_exact(datetime(2026, 9, 12, 9, tzinfo=NY))  # Saturday 09:00
    assert not effective.is_working(weekend)  # the crew is not there
    assert effective.is_working(9)  # Monday 09:00, both agree


def test_a_task_whose_calendars_never_agree_is_rejected():
    """Days versus nights: the empty intersection is a validation error, not an
    infeasible solve nobody can explain."""
    project = make(
        tasks=(Task(id="a", duration=8, calendar_id="5x8"),),
        dependencies=(),
        resources=(Resource(id="owl", capacity=1, calendar_id="nights"),),
        assignments=(Assignment(task_id="a", resource_id="owl"),),
    )
    assert any("never agree" in p for p in problems_of(project))


def test_resource_exceptions_layer_over_a_shared_base_calendar():
    """Twenty resources share one calendar; one crane carries its own breakdown."""
    breakdown = CalendarException(
        start_date=datetime(2026, 9, 9, tzinfo=NY).date(),
        end_date=datetime(2026, 9, 9, tzinfo=NY).date(),
        reason="crane down",
    )
    project = make(
        tasks=(Task(id="a", duration=8),),
        dependencies=(),
        resources=(
            Resource(id="crane", capacity=1, exceptions=(breakdown,)),
            Resource(id="crew", capacity=4),
        ),
        assignments=(Assignment(task_id="a", resource_id="crane"),),
    )
    index = validate_project(project)
    wednesday = 2 * 24 + 9
    assert not index.effective_calendar("a").is_working(wednesday)
    assert index.calendar_for_resource("crew").is_working(wednesday)


def test_effective_calendars_are_cached_by_calendar_not_by_task():
    """Tasks sharing a trade and a crew share an answer."""
    project = make(
        tasks=(Task(id="a", duration=8), Task(id="b", duration=8)),
        resources=(Resource(id="crew", capacity=4),),
        assignments=(
            Assignment(task_id="a", resource_id="crew"),
            Assignment(task_id="b", resource_id="crew"),
        ),
    )
    index = validate_project(project)
    assert index.effective_calendar("a") is index.effective_calendar("b")


# -- horizon ---------------------------------------------------------------


def test_the_horizon_is_derived_from_the_longest_chain_not_the_sequential_sum():
    """Twenty independent tasks are one task long, not twenty.

    A horizon big enough to run a project strictly one task at a time is
    enormous and almost never needed; materialising calendars across it was the
    dominant cost of validating a large project. Resource contention is
    accounted for by `solve.fitted_index`, not here — a calendar horizon is not
    a schedule bound.
    """
    tasks = tuple(Task(id=f"t{n}", duration=8) for n in range(20))
    index = validate_project(make(tasks=tasks, dependencies=()))
    available = index.effective_calendar("t0").total_work
    assert available >= 8  # the chain
    assert available < sum(t.duration for t in tasks)  # not the sum


def test_a_chain_of_dependencies_does_widen_the_horizon():
    tasks = tuple(Task(id=f"t{n}", duration=8) for n in range(6))
    links = tuple(Dependency(predecessor_id=f"t{n}", successor_id=f"t{n + 1}") for n in range(5))
    index = validate_project(make(tasks=tasks, dependencies=links))
    assert index.effective_calendar("t0").total_work >= 48


def test_an_explicit_horizon_is_respected():
    index = validate_project(make(planning_horizon=24 * 90))
    assert index.horizon == 24 * 90


def test_work_that_cannot_fit_the_given_horizon_is_reported():
    project = make(tasks=(Task(id="a", duration=500),), dependencies=(), planning_horizon=48)
    assert any("offers only" in p for p in problems_of(project))


def test_pay_rules_references_are_checked():
    project = make(
        pay_rules=(PayRules(id="local"),),
        resources=(Resource(id="crew", capacity=2, pay_rules_id="other"),),
    )
    assert any("unknown pay rules 'other'" in p for p in problems_of(project))
