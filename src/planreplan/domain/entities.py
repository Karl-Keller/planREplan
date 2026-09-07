"""Project entities — the ubiquitous language of the system.

Normative reference: ``docs/03-domain-model.md``. Names here are the names used
in code, docs, and conversation, with the meanings given there.

Models carry only the rules that keep a *single* entity coherent. Anything
needing to see the whole project — id uniqueness, reference integrity, WBS
shape, effective calendars — belongs to
:func:`planreplan.domain.validation.validate_project`, which reports every
problem at once instead of stopping at the first.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from planreplan.domain.calendar import Calendar, CalendarException, DayOfWeek
from planreplan.domain.schedule import Baseline
from planreplan.domain.time_axis import Tick, TimeAxis


class Endpoint(StrEnum):
    """Which end of a task a dependency refers to."""

    START = "start"
    FINISH = "finish"


class DependencyKind(StrEnum):
    """All four kinds, supported from the start.

    Construction schedules use SS+lag pervasively, so treating FS as the
    interesting case and the rest as an extension would be backwards.
    """

    FS = "FS"
    SS = "SS"
    FF = "FF"
    SF = "SF"

    @property
    def predecessor_endpoint(self) -> Endpoint:
        return Endpoint.FINISH if self in (DependencyKind.FS, DependencyKind.FF) else Endpoint.START

    @property
    def successor_endpoint(self) -> Endpoint:
        return Endpoint.START if self in (DependencyKind.FS, DependencyKind.SS) else Endpoint.FINISH


class TaskStatus(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"


class PayClass(StrEnum):
    """How an hour is paid. Positional premiums are independent of thresholds."""

    REGULAR = "regular"
    DAILY_OVERTIME = "daily_overtime"
    WEEKLY_OVERTIME = "weekly_overtime"
    DAY_PREMIUM = "day_premium"
    SHIFT_PREMIUM = "shift_premium"
    HOLIDAY = "holiday"


class PayRules(BaseModel):
    """A labour agreement. Attaches to a resource, not to a calendar.

    Two crews working identical hours can be paid under different agreements,
    which is the common case on a multi-trade site. Thresholds are data because
    real agreements disagree: one permitting a compressed 4x10 week without
    daily overtime and one charging it are both ordinary, and a hardcoded 8/40
    would silently pick a side.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str = ""
    week_starts_on: DayOfWeek = DayOfWeek.MONDAY
    daily_regular_hours: int = Field(default=8, gt=0)
    weekly_regular_hours: int = Field(default=40, gt=0)
    daily_overtime_multiplier: float = Field(default=1.5, ge=1.0)
    weekly_overtime_multiplier: float = Field(default=1.5, ge=1.0)
    day_premiums: dict[DayOfWeek, float] = Field(default_factory=dict)
    shift_premiums: dict[str, float] = Field(default_factory=dict)
    holiday_premium: float = Field(default=1.0, ge=1.0)

    @model_validator(mode="after")
    def _weekly_covers_at_least_a_day(self) -> PayRules:
        if self.weekly_regular_hours < self.daily_regular_hours:
            raise ValueError(
                f"pay rules {self.id!r}: weekly_regular_hours "
                f"({self.weekly_regular_hours}) is below daily_regular_hours "
                f"({self.daily_regular_hours})"
            )
        for day, multiplier in self.day_premiums.items():
            if multiplier < 1.0:
                raise ValueError(
                    f"pay rules {self.id!r}: {DayOfWeek(day).name} premium {multiplier} "
                    "is below straight time; premiums multiply the base rate"
                )
        for shift, multiplier in self.shift_premiums.items():
            if multiplier < 1.0:
                raise ValueError(
                    f"pay rules {self.id!r}: shift {shift!r} premium {multiplier} "
                    "is below straight time"
                )
        return self


class Task(BaseModel):
    """A node in the WBS. Only leaves carry duration and assignments.

    ``wbs_path`` is deliberately absent: the design calls it display metadata
    derived from the tree and never an identity, so it is computed by
    :class:`~planreplan.domain.validation.ProjectIndex` rather than stored
    where it could drift from the tree it describes.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str = ""
    duration: int = Field(default=0, ge=0)
    calendar_id: str | None = None
    parent_id: str | None = None
    is_milestone: bool = False
    deadline: Tick | None = None
    hard_commitment: bool = False
    status: TaskStatus = TaskStatus.NOT_STARTED

    @model_validator(mode="after")
    def _milestones_are_instantaneous(self) -> Task:
        if self.is_milestone and self.duration:
            raise ValueError(f"milestone {self.id!r} has duration {self.duration}; it must be zero")
        return self


class Dependency(BaseModel):
    """A precedence link. ``lag`` is elapsed ticks, not working ticks.

    Construction's SS+lag links are mostly cure, dry, and settle times, which
    run on wall-clock rather than crew time. A successor's start must still
    land on a working tick, so a lag expiring mid-weekend resolves forward.
    Negative lag (a lead) is permitted, as in P6.
    """

    model_config = ConfigDict(frozen=True)

    predecessor_id: str
    successor_id: str
    kind: DependencyKind = DependencyKind.FS
    lag: int = 0

    @model_validator(mode="after")
    def _no_self_dependency(self) -> Dependency:
        if self.predecessor_id == self.successor_id:
            raise ValueError(f"task {self.predecessor_id!r} depends on itself")
        return self


class Resource(BaseModel):
    """A renewable resource with integer capacity per tick.

    ``exceptions`` layer over the base calendar rather than replacing it, so
    twenty resources can share one "standard 5x8" while a single crane carries
    its own breakdown window. Layering is what keeps calendar indexes small
    enough to hold one per distinct resource mix.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str = ""
    capacity: int = Field(gt=0)
    calendar_id: str | None = None
    pay_rules_id: str | None = None
    exceptions: tuple[CalendarException, ...] = ()


class Assignment(BaseModel):
    """Constant demand on a resource for a task's full duration (v1)."""

    model_config = ConfigDict(frozen=True)

    task_id: str
    resource_id: str
    demand: int = Field(default=1, gt=0)


class Project(BaseModel):
    """A project: entities plus the axis they are all dated against.

    ``planning_horizon`` bounds calendar materialisation. It is derived when
    absent (see :func:`~planreplan.domain.validation.validate_project`) so that
    ordinary use never has to think about it, but stays settable for a project
    that knows its own end better than a heuristic does.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    id: str
    name: str = ""
    axis: TimeAxis
    calendars: tuple[Calendar, ...]
    default_calendar_id: str
    pay_rules: tuple[PayRules, ...] = ()
    default_pay_rules_id: str | None = None
    tasks: tuple[Task, ...] = ()
    dependencies: tuple[Dependency, ...] = ()
    resources: tuple[Resource, ...] = ()
    assignments: tuple[Assignment, ...] = ()
    baselines: tuple[Baseline, ...] = ()
    planning_horizon: Tick | None = Field(default=None, gt=0)
