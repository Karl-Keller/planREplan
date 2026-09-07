"""Entity-level rules: what a single entity can check without seeing the project."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from planreplan.domain import (
    Assignment,
    Dependency,
    DependencyKind,
    Endpoint,
    PayRules,
    Resource,
    Task,
    TaskStatus,
)

NY = ZoneInfo("America/New_York")
EPOCH = datetime(2026, 9, 7, tzinfo=NY)


def test_a_milestone_cannot_have_duration():
    with pytest.raises(ValidationError, match="must be zero"):
        Task(id="m", is_milestone=True, duration=4)


def test_a_milestone_with_zero_duration_is_fine():
    assert Task(id="m", is_milestone=True).duration == 0


def test_a_task_cannot_depend_on_itself():
    with pytest.raises(ValidationError, match="depends on itself"):
        Dependency(predecessor_id="a", successor_id="a")


def test_negative_lag_is_permitted_as_a_lead():
    assert Dependency(predecessor_id="a", successor_id="b", lag=-8).lag == -8


def test_tasks_default_to_not_started():
    assert Task(id="t", duration=8).status is TaskStatus.NOT_STARTED


def test_resource_capacity_must_be_positive():
    with pytest.raises(ValidationError):
        Resource(id="crew", capacity=0)


def test_assignment_demand_must_be_positive():
    with pytest.raises(ValidationError):
        Assignment(task_id="t", resource_id="crew", demand=0)


@pytest.mark.parametrize(
    ("kind", "predecessor", "successor"),
    [
        (DependencyKind.FS, Endpoint.FINISH, Endpoint.START),
        (DependencyKind.SS, Endpoint.START, Endpoint.START),
        (DependencyKind.FF, Endpoint.FINISH, Endpoint.FINISH),
        (DependencyKind.SF, Endpoint.START, Endpoint.FINISH),
    ],
)
def test_dependency_kinds_name_the_endpoints_they_read(kind, predecessor, successor):
    """These drive summary expansion, so they are pinned rather than inferred."""
    assert kind.predecessor_endpoint is predecessor
    assert kind.successor_endpoint is successor


def test_weekly_regular_hours_cannot_fall_below_daily():
    with pytest.raises(ValidationError, match="below daily_regular_hours"):
        PayRules(id="local", daily_regular_hours=10, weekly_regular_hours=8)


def test_a_four_by_ten_agreement_is_expressible():
    """The case a hardcoded 8/40 would silently decide."""
    exempt = PayRules(id="4x10", daily_regular_hours=10, weekly_regular_hours=40)
    charged = PayRules(id="strict", daily_regular_hours=8, weekly_regular_hours=40)
    assert exempt.daily_regular_hours != charged.daily_regular_hours


def test_premiums_cannot_undercut_straight_time():
    with pytest.raises(ValidationError, match="below straight time"):
        PayRules(id="odd", day_premiums={5: 0.5})


def test_entities_are_frozen():
    task = Task(id="t", duration=8)
    with pytest.raises(ValidationError):
        task.duration = 16
