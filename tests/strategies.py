"""Hypothesis strategies for generated projects.

Core test infrastructure per ADR-7: CPM and repair invariants are algebraic
laws, and laws want arbitrary inputs rather than the handful of shapes a person
thinks to write down.

Every generated project is *valid* by construction. A generator that mostly
produced rejects would spend its budget rediscovering the validation rules
instead of exercising the code under test, so acyclicity comes from ordering
rather than filtering, demand is drawn against capacity, and one shared
calendar keeps every effective calendar non-empty.

Lives in ``tests/`` rather than the package because it needs hypothesis, and
the shipped package must not depend on a test-only library.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from hypothesis import strategies as st

from planreplan.domain import (
    Assignment,
    Calendar,
    CalendarException,
    DayOfWeek,
    Dependency,
    DependencyKind,
    PayRules,
    Project,
    Resource,
    Shift,
    Task,
    TimeAxis,
)

NY = ZoneInfo("America/New_York")

#: Zones chosen to cover northern and southern DST and a zone with none at all,
#: since the axis's one hard job is absorbing transitions.
TIMEZONES = ("America/New_York", "Europe/Berlin", "Australia/Sydney", "UTC")


@st.composite
def time_axes(draw: st.DrawFn) -> TimeAxis:
    zone = ZoneInfo(draw(st.sampled_from(TIMEZONES)))
    epoch = datetime(2026, draw(st.integers(1, 12)), draw(st.integers(1, 28)), tzinfo=zone)
    return TimeAxis(epoch=epoch, ticks_per_day=draw(st.sampled_from([24, 48])))


@st.composite
def shifts(draw: st.DrawFn) -> Shift:
    """A shift bounded inside its day, on the hour.

    Boundaries stay on the hour because a generated axis may be hourly, and a
    06:30 boundary there is a modelling error the index rightly refuses. Testing
    that refusal is the job of an explicit test, not of every generated project.
    """
    start_hour = draw(st.integers(0, 16))
    length = draw(st.integers(1, 24 - start_hour))
    return Shift(
        name=draw(st.sampled_from(["day", "swing", "night"])),
        start_minute=start_hour * 60,
        end_minute=(start_hour + length) * 60,
    )


@st.composite
def calendars(draw: st.DrawFn, *, identifier: str = "cal") -> Calendar:
    """A calendar with at least one working day, so no project is unschedulable."""
    days = draw(st.lists(st.sampled_from(list(DayOfWeek)), min_size=1, max_size=7, unique=True))
    shift = draw(shifts())
    return Calendar(id=identifier, name=identifier, week_pattern=dict.fromkeys(days, (shift,)))


@st.composite
def pay_rules(draw: st.DrawFn, *, identifier: str = "cba") -> PayRules:
    daily = draw(st.integers(6, 12))
    return PayRules(
        id=identifier,
        week_starts_on=draw(st.sampled_from(list(DayOfWeek))),
        daily_regular_hours=daily,
        weekly_regular_hours=draw(st.integers(daily, 60)),
        daily_overtime_multiplier=draw(st.sampled_from([1.0, 1.5, 2.0])),
        weekly_overtime_multiplier=draw(st.sampled_from([1.0, 1.5, 2.0])),
        day_premiums=draw(
            st.dictionaries(
                st.sampled_from(list(DayOfWeek)), st.sampled_from([1.0, 1.5, 2.0]), max_size=3
            )
        ),
    )


@st.composite
def projects(
    draw: st.DrawFn,
    *,
    max_tasks: int = 8,
    max_resources: int = 3,
    allow_summaries: bool = True,
    kinds: tuple[DependencyKind, ...] = (DependencyKind.FS,),
) -> Project:
    """A valid project: acyclic, resourced, and schedulable on one calendar.

    Dependencies run strictly from a lower index to a higher one, which makes
    the graph acyclic by construction rather than by rejection.

    ``kinds`` defaults to FS alone because it is the only kind that expands
    across a summary on either side, so summaries can appear without generating
    links validation would refuse. Pass all four with ``allow_summaries=False``
    to exercise the full precedence algebra.
    """
    axis = draw(time_axes())
    calendar = draw(calendars())
    rules = draw(pay_rules())

    leaf_count = draw(st.integers(1, max_tasks))
    leaves: list[Task] = []
    for position in range(leaf_count):
        milestone = draw(st.booleans()) and position > 0
        leaves.append(
            Task(
                id=f"t{position}",
                name=f"task {position}",
                duration=0 if milestone else draw(st.integers(1, 24)),
                is_milestone=milestone,
                hard_commitment=draw(st.booleans()),
            )
        )

    tasks: list[Task] = list(leaves)
    if allow_summaries and leaf_count >= 2 and draw(st.booleans()):
        # Re-parent a prefix of the leaves under a summary. The summary carries
        # no duration, which is what validation requires of a non-leaf.
        adopted = draw(st.integers(2, leaf_count))
        tasks = [Task(id="s", name="summary")]
        tasks += [t.model_copy(update={"parent_id": "s"}) for t in leaves[:adopted]]
        tasks += leaves[adopted:]

    dependencies: list[Dependency] = []
    for successor in range(1, leaf_count):
        if draw(st.booleans()):
            predecessor = draw(st.integers(0, successor - 1))
            dependencies.append(
                Dependency(
                    predecessor_id=f"t{predecessor}",
                    successor_id=f"t{successor}",
                    kind=draw(st.sampled_from(list(kinds))),
                    lag=draw(st.integers(-4, 24)),
                )
            )

    resources: list[Resource] = []
    assignments: list[Assignment] = []
    for position in range(draw(st.integers(0, max_resources))):
        capacity = draw(st.integers(1, 4))
        resources.append(
            Resource(
                id=f"r{position}",
                name=f"resource {position}",
                capacity=capacity,
                pay_rules_id=rules.id,
            )
        )
        for task in leaves:
            if draw(st.booleans()):
                assignments.append(
                    Assignment(
                        task_id=task.id,
                        resource_id=f"r{position}",
                        demand=draw(st.integers(1, capacity)),
                    )
                )

    return Project(
        id="generated",
        name="generated project",
        axis=axis,
        calendars=(calendar,),
        default_calendar_id=calendar.id,
        pay_rules=(rules,),
        default_pay_rules_id=rules.id,
        tasks=tuple(tasks),
        dependencies=tuple(dependencies),
        resources=tuple(resources),
        assignments=tuple(assignments),
    )


__all__ = [
    "CalendarException",
    "calendars",
    "pay_rules",
    "projects",
    "shifts",
    "time_axes",
]
