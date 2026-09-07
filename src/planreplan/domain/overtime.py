"""Overtime aggregation — a pure function over domain objects.

``docs/03-domain-model.md``: weekly overtime is a property of a *resource
across all its tasks*, never of a single task, because a crew crosses forty
hours from the combination of its assignments. Aggregation therefore runs over
a whole set of task spans and attributes hours to resources and pay weeks.

v1 represents and reports overtime; it does not optimise against it. When cost
objectives arrive, minimising premium time becomes another term in the
``STABLE_RESOLVE`` objective without a schema change.

Aggregation reads a ``Schedule``. It took a bare mapping of spans while no
schedule type existed; the arithmetic did not change when the type arrived.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from datetime import date, datetime, time, timedelta
from itertools import pairwise

from pydantic import BaseModel, ConfigDict

from planreplan.domain.calendar import Calendar, DayOfWeek
from planreplan.domain.entities import PayClass, PayRules
from planreplan.domain.schedule import Schedule
from planreplan.domain.time_axis import Tick, TimeAxis
from planreplan.domain.validation import ProjectIndex

#: A span of a task on the axis: start inclusive, finish exclusive.
Span = tuple[Tick, Tick]

#: Tie-break when two pay classes carry the same multiplier. Fixed so that a
#: report is reproducible (NFR1) rather than dependent on iteration order.
_PRECEDENCE: tuple[PayClass, ...] = (
    PayClass.HOLIDAY,
    PayClass.WEEKLY_OVERTIME,
    PayClass.DAILY_OVERTIME,
    PayClass.DAY_PREMIUM,
    PayClass.SHIFT_PREMIUM,
    PayClass.REGULAR,
)

_DEFAULT_RULES = PayRules(id="__default__")


class OvertimeReport(BaseModel):
    """What one resource accrued in one pay week.

    Two quantities, answering two questions. ``*_ticks`` counts wall-clock
    engagement of the resource: when the crew was on site, which is what the
    per-person thresholds in a labour agreement are stated against.
    ``*_person_ticks`` weights each of those by the units actually engaged,
    giving labour content, which is what a cost ever gets built from.

    Both are needed because neither answers the other's question. A crew of
    four on an ordinary week worked 40 hours and 160 person-hours; charging
    overtime on the second number would invent 120 hours nobody worked.

    Counted in ticks rather than hours so the arithmetic stays integer
    (design rule 5); :meth:`hours` converts at the reporting boundary.
    """

    model_config = ConfigDict(frozen=True)

    resource_id: str
    week_start: Tick
    regular_ticks: int
    premium_ticks: dict[PayClass, int]
    regular_person_ticks: int = 0
    premium_person_ticks: dict[PayClass, int] = {}

    @property
    def total_ticks(self) -> int:
        return self.regular_ticks + sum(self.premium_ticks.values())

    @property
    def total_person_ticks(self) -> int:
        return self.regular_person_ticks + sum(self.premium_person_ticks.values())

    def hours(self, axis: TimeAxis, ticks: int) -> float:
        return ticks * 24 / axis.ticks_per_day


class OvertimeResolutionError(ValueError):
    """The axis cannot express an hour, so hour-based thresholds are meaningless."""


def _ticks_per_hour(axis: TimeAxis) -> int:
    if axis.ticks_per_day % 24:
        raise OvertimeResolutionError(
            f"overtime accounting needs an axis on which an hour is a whole number of "
            f"ticks; ticks_per_day={axis.ticks_per_day} cannot express an hourly "
            "threshold. Use 24, 48, or 96."
        )
    return axis.ticks_per_day // 24


def _reservations(
    index: ProjectIndex, spans: Mapping[str, Span], resource_id: str
) -> list[tuple[Tick, Tick, int]]:
    """Working-time segments this resource is held for, with unit counts.

    A resource is *reserved* across a task's whole span including internal
    gaps, but only *working* during the effective calendar's working time —
    nobody is paid for the weekend a task straddles. Each segment carries the
    assignment's demand so utilisation is not thrown away.
    """
    segments: list[tuple[Tick, Tick, int]] = []
    for task_id, (start, finish) in spans.items():
        for assignment in index.assignments_for(task_id):
            if assignment.resource_id != resource_id:
                continue
            for block in index.effective_calendar(task_id).blocks:
                overlap_start = max(block.start, start)
                overlap_end = min(block.end, finish)
                if overlap_start < overlap_end:
                    segments.append((overlap_start, overlap_end, assignment.demand))
    return segments


def _engaged(segments: Sequence[tuple[Tick, Tick, int]]) -> Iterator[tuple[Tick, int]]:
    """Each engaged tick, with how many units were engaged on it.

    A sweep over segment endpoints rather than a union of intervals. The union
    answered *whether* the crew was on site and discarded *how much of it* —
    two of four units looked identical to all four, which is exactly the
    utilisation a cost is built from.
    """
    deltas: dict[Tick, int] = defaultdict(int)
    for start, end, demand in segments:
        deltas[start] += demand
        deltas[end] -= demand
    points = sorted(deltas)
    engaged = 0
    for point, following in pairwise(points):
        engaged += deltas[point]
        if engaged > 0:
            for tick in range(point, following):
                yield tick, engaged


def _week_start(day: date, starts_on: DayOfWeek) -> date:
    return day - timedelta(days=(day.weekday() - int(starts_on)) % 7)


def _classify(
    rules: PayRules,
    *,
    weekday: DayOfWeek,
    shift_name: str | None,
    is_holiday: bool,
    day_ticks: int,
    week_ticks: int,
    daily_limit: int,
    weekly_limit: int,
) -> PayClass:
    """The single pay class this tick earns.

    Each tick is classified exactly once, at the highest applicable multiplier.
    That is the no-pyramiding rule near-universal in construction agreements,
    and it keeps ``regular + sum(premium) == total`` true, which a report that
    counted an hour in several categories could not. Agreements that do stack
    premiums are a v2 concern.

    The comparison is on the multipliers themselves rather than a hardcoded
    order of classes, so an agreement paying Sunday at double and weekly
    overtime at time-and-a-half resolves correctly without special-casing.
    """
    candidates: list[tuple[PayClass, float]] = [(PayClass.REGULAR, 1.0)]
    if day_ticks >= daily_limit:
        candidates.append((PayClass.DAILY_OVERTIME, rules.daily_overtime_multiplier))
    if week_ticks >= weekly_limit:
        candidates.append((PayClass.WEEKLY_OVERTIME, rules.weekly_overtime_multiplier))
    if weekday in rules.day_premiums:
        candidates.append((PayClass.DAY_PREMIUM, rules.day_premiums[weekday]))
    if shift_name is not None and shift_name in rules.shift_premiums:
        candidates.append((PayClass.SHIFT_PREMIUM, rules.shift_premiums[shift_name]))
    if is_holiday:
        candidates.append((PayClass.HOLIDAY, rules.holiday_premium))
    best = max(multiplier for _, multiplier in candidates)
    return next(
        pay_class
        for pay_class in _PRECEDENCE
        if any(c == pay_class and m == best for c, m in candidates)
    )


def _rules_for(index: ProjectIndex, resource_id: str) -> PayRules:
    """A resource's agreement, falling back to the project's, then to defaults."""
    resource = index.resource(resource_id)
    by_id = {p.id: p for p in index.project.pay_rules}
    chosen = resource.pay_rules_id or index.project.default_pay_rules_id
    return by_id.get(chosen, _DEFAULT_RULES) if chosen else _DEFAULT_RULES


def _base_calendar(index: ProjectIndex, resource_id: str) -> Calendar:
    resource = index.resource(resource_id)
    by_id = {c.id: c for c in index.project.calendars}
    base = by_id[resource.calendar_id or index.project.default_calendar_id]
    if resource.exceptions:
        return base.model_copy(update={"exceptions": base.exceptions + resource.exceptions})
    return base


def _ticks(runs: Sequence[Span]) -> Iterator[Tick]:
    for start, end in runs:
        yield from range(start, end)


def overtime_report(index: ProjectIndex, schedule: Schedule) -> tuple[OvertimeReport, ...]:
    """Regular and premium ticks per resource per pay week.

    Hours are counted as **wall-clock engagement of the resource**, not
    person-hours weighted by ``Assignment.demand``. A crew of four with two
    units on each of two concurrent tasks worked one shift, not two, and the
    thresholds in a labour agreement are per person per day. Demand-weighted
    person-hours are a v2 refinement.

    Args:
        index: a validated project.
        schedule: the schedule whose spans the resources worked.

    Raises:
        OvertimeResolutionError: if the axis cannot express a whole hour.
    """
    spans = schedule.spans()
    axis = index.project.axis
    per_hour = _ticks_per_hour(axis)
    reports: list[OvertimeReport] = []

    for resource in index.project.resources:
        segments = _reservations(index, spans, resource.id)
        if not segments:
            continue
        rules = _rules_for(index, resource.id)
        calendar = _base_calendar(index, resource.id)
        daily_limit = rules.daily_regular_hours * per_hour
        weekly_limit = rules.weekly_regular_hours * per_hour

        weeks: dict[date, dict[PayClass, int]] = {}
        person_weeks: dict[date, dict[PayClass, int]] = {}
        day_totals: dict[date, int] = {}
        week_totals: dict[date, int] = {}

        for tick, engaged in _engaged(segments):
            moment = axis.to_datetime(tick)
            day = moment.date()
            week = _week_start(day, rules.week_starts_on)
            shift = calendar.shift_at(day, moment.hour * 60 + moment.minute)
            pay_class = _classify(
                rules,
                weekday=DayOfWeek(day.weekday()),
                shift_name=shift.name if shift else None,
                is_holiday=calendar.is_holiday(day),
                day_ticks=day_totals.get(day, 0),
                week_ticks=week_totals.get(week, 0),
                daily_limit=daily_limit,
                weekly_limit=weekly_limit,
            )
            tally = weeks.setdefault(week, {})
            tally[pay_class] = tally.get(pay_class, 0) + 1
            person_tally = person_weeks.setdefault(week, {})
            person_tally[pay_class] = person_tally.get(pay_class, 0) + engaged
            # Thresholds accumulate wall-clock engagement, not person-hours: an
            # agreement's 8 and 40 are stated per person, and the crew's own
            # hours are the best available proxy for its members'.
            day_totals[day] = day_totals.get(day, 0) + 1
            week_totals[week] = week_totals.get(week, 0) + 1

        for week in sorted(weeks):
            tallies = dict(weeks[week])
            person_tallies = dict(person_weeks[week])
            reports.append(
                OvertimeReport(
                    resource_id=resource.id,
                    week_start=axis.to_tick(
                        datetime.combine(week, time.min, tzinfo=axis.epoch.tzinfo)
                    ),
                    regular_ticks=tallies.pop(PayClass.REGULAR, 0),
                    premium_ticks=tallies,
                    regular_person_ticks=person_tallies.pop(PayClass.REGULAR, 0),
                    premium_person_ticks=person_tallies,
                )
            )
    return tuple(reports)
