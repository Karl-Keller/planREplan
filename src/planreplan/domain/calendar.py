"""Calendars as predicates over the tick axis (ADR-8).

A calendar never defines the axis; it answers questions about it. The data
form (:class:`Calendar`) is a weekly shift pattern plus dated exceptions. The
query form (:class:`CalendarIndex`) materialises that against a
:class:`~planreplan.domain.time_axis.TimeAxis` into sorted working blocks,
which is what both the solver and the monitor actually consult.

Blocks rather than a dense per-tick array: see ``docs/03-domain-model.md``.
A five-day eight-hour calendar over three years is ~780 blocks against ~26,000
ticks, the representation is closed under intersection (effective calendars are
intersections, and there can be one per distinct resource mix), and the linear
walk that builds the CP-SAT table of ADR-8 is a walk over blocks. The cost is
O(log n) lookup instead of O(1), which is nothing beside model construction.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import IntEnum
from itertools import pairwise
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from planreplan.domain.time_axis import Tick, TimeAxis

MINUTES_PER_DAY: Final = 1_440


class DayOfWeek(IntEnum):
    """Aligned with :meth:`datetime.date.weekday` so no translation is needed."""

    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6


class Shift(BaseModel):
    """A working window within a local day, in minutes from local midnight.

    Minutes rather than whole hours because construction start times are
    routinely half-past — a 06:30 start is ordinary. Whether a given boundary
    is *usable* depends on the axis: 06:30 needs ``ticks_per_day >= 48``, and
    :meth:`CalendarIndex.build` refuses rather than rounding.

    ``end_minute`` is exclusive and may equal 1440 to mean local midnight.
    """

    model_config = ConfigDict(frozen=True)

    name: str = "day"
    start_minute: int = Field(ge=0, lt=MINUTES_PER_DAY)
    end_minute: int = Field(gt=0, le=MINUTES_PER_DAY)

    @model_validator(mode="after")
    def _end_follows_start(self) -> Shift:
        if self.end_minute <= self.start_minute:
            raise ValueError(
                f"shift {self.name!r} ends at {self.end_minute} which is not after "
                f"its start at {self.start_minute}; overnight shifts are modelled as "
                "two shifts on consecutive days in v1"
            )
        return self

    @property
    def minutes(self) -> int:
        return self.end_minute - self.start_minute


def _validate_day_shifts(shifts: Sequence[Shift], where: str) -> None:
    """Shifts within one day must be ordered and disjoint."""
    for earlier, later in pairwise(shifts):
        if later.start_minute < earlier.end_minute:
            raise ValueError(
                f"shifts on {where} overlap or are unordered: "
                f"{earlier.name!r} ends at {earlier.end_minute}, "
                f"{later.name!r} starts at {later.start_minute}"
            )


class CalendarException(BaseModel):
    """A dated override of the weekly pattern.

    One mechanism for holidays, weather shutdowns, equipment breakdowns and an
    inspector's absence — ``docs/03-domain-model.md`` asks for these to be
    handled uniformly, and folding resource unavailability in here is what
    keeps the ADR-8 block tables small.

    ``holiday`` is separate from ``working`` because the two are independent: a
    holiday is normally non-working, but a crew called out on one is working a
    holiday, and that is exactly when the holiday premium applies.
    """

    model_config = ConfigDict(frozen=True)

    start_date: date
    end_date: date
    working: bool = False
    holiday: bool = False
    shifts: tuple[Shift, ...] = ()
    reason: str = ""

    @model_validator(mode="after")
    def _check(self) -> CalendarException:
        if self.end_date < self.start_date:
            raise ValueError("exception end_date precedes start_date")
        if not self.working and self.shifts:
            raise ValueError("a non-working exception cannot carry shifts")
        _validate_day_shifts(self.shifts, f"exception {self.reason or self.start_date}")
        return self

    def covers(self, day: date) -> bool:
        return self.start_date <= day <= self.end_date


class Calendar(BaseModel):
    """Weekly working pattern plus dated exceptions. Pure data, no axis."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str = ""
    week_pattern: dict[DayOfWeek, tuple[Shift, ...]] = Field(default_factory=dict)
    exceptions: tuple[CalendarException, ...] = ()

    @model_validator(mode="after")
    def _check_pattern(self) -> Calendar:
        for day, shifts in self.week_pattern.items():
            _validate_day_shifts(shifts, DayOfWeek(day).name)
        return self

    def shifts_on(self, day: date) -> tuple[Shift, ...]:
        """Working shifts for a local date; the last matching exception wins.

        Last rather than first so that a narrow override declared after a broad
        one behaves the way a reader expects — a site-wide shutdown followed by
        a single-crew callout on one of those days.
        """
        for exception in reversed(self.exceptions):
            if exception.covers(day):
                return exception.shifts if exception.working else ()
        return self.week_pattern.get(DayOfWeek(day.weekday()), ())

    def shift_at(self, day: date, minute: int) -> Shift | None:
        """The shift covering a local minute, or ``None`` outside working time."""
        for shift in self.shifts_on(day):
            if shift.start_minute <= minute < shift.end_minute:
                return shift
        return None

    def is_holiday(self, day: date) -> bool:
        """Whether any exception marks this date a holiday, working or not."""
        return any(e.covers(day) and e.holiday for e in self.exceptions)


@dataclass(frozen=True, slots=True)
class WorkBlock:
    """A maximal run of working ticks, tagged with the work preceding it.

    ``work_before`` is what turns a binary search into the prefix function W of
    ADR-8: within a block, W is affine.
    """

    start: Tick
    end: Tick
    work_before: int

    @property
    def length(self) -> int:
        return self.end - self.start


def _merge_adjacent(blocks: Iterable[tuple[Tick, Tick]]) -> list[WorkBlock]:
    """Coalesce touching runs and stamp the cumulative work count.

    Adjacent runs are common and worth collapsing: a continuous seven-day
    calendar reduces to a single block instead of one per day.
    """
    merged: list[list[Tick]] = []
    for start, end in blocks:
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    out: list[WorkBlock] = []
    work = 0
    for start, end in merged:
        out.append(WorkBlock(start=start, end=end, work_before=work))
        work += end - start
    return out


def _local_moment(day: date, minute: int, tz: object) -> datetime:
    """Local wall-clock datetime for a minute offset, rolling into the next day."""
    if minute >= MINUTES_PER_DAY:
        day += timedelta(days=minute // MINUTES_PER_DAY)
        minute %= MINUTES_PER_DAY
    return datetime.combine(day, time(minute // 60, minute % 60), tzinfo=tz)  # type: ignore[arg-type]


class CalendarIndex:
    """Materialised calendar: the queries the solver and monitor actually make.

    Built eagerly to an explicit horizon. Lazy extension would avoid the
    commitment but complicates intersection and determinism for no benefit at
    the sizes involved; querying past the horizon raises rather than quietly
    reporting non-working time, which would look exactly like a project that
    stops in mid-air.
    """

    __slots__ = ("_blocks", "_starts", "_work_marks", "axis", "horizon")

    def __init__(self, blocks: Sequence[WorkBlock], axis: TimeAxis, horizon: Tick) -> None:
        self._blocks = tuple(blocks)
        self._starts = [b.start for b in self._blocks]
        self._work_marks = [b.work_before for b in self._blocks]
        self.axis = axis
        self.horizon = horizon

    @classmethod
    def build(cls, calendar: Calendar, axis: TimeAxis, horizon: Tick) -> CalendarIndex:
        """Materialise ``calendar`` over ``[0, horizon)`` on ``axis``.

        Shift boundaries are converted through :meth:`TimeAxis.to_tick_exact`,
        so a boundary that does not land on the tick grid is an error naming
        the resolution that would fit it. Boundaries are converted per local
        day, which is what absorbs DST: a wall-clock eight-hour shift is seven
        ticks on a spring-forward day and nine on a fall-back day, because that
        is how long the crew is physically on site.
        """
        if horizon < 0:
            raise ValueError("horizon must be non-negative")
        try:
            axis.to_datetime(horizon)
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError(
                f"horizon {horizon} extends beyond a representable date at "
                f"ticks_per_day={axis.ticks_per_day}"
            ) from exc
        tz = axis.epoch.tzinfo
        runs: list[tuple[Tick, Tick]] = []
        day = axis.to_datetime(0).date()
        while True:
            day_start = axis.to_tick(_local_moment(day, 0, tz))
            if day_start >= horizon:
                break
            for shift in calendar.shifts_on(day):
                start = axis.to_tick_exact(_local_moment(day, shift.start_minute, tz))
                end = axis.to_tick_exact(_local_moment(day, shift.end_minute, tz))
                runs.append((max(start, 0), min(end, horizon)))
            day += timedelta(days=1)
        return cls(_merge_adjacent(runs), axis, horizon)

    @property
    def blocks(self) -> tuple[WorkBlock, ...]:
        return self._blocks

    @property
    def total_work(self) -> int:
        """Working ticks in the whole horizon."""
        if not self._blocks:
            return 0
        last = self._blocks[-1]
        return last.work_before + last.length

    def _block_at_or_before(self, tick: Tick) -> WorkBlock | None:
        position = bisect_right(self._starts, tick) - 1
        return self._blocks[position] if position >= 0 else None

    def _check_horizon(self, tick: Tick) -> None:
        if not 0 <= tick <= self.horizon:
            raise ValueError(f"tick {tick} lies outside the indexed horizon [0, {self.horizon}]")

    def is_working(self, tick: Tick) -> bool:
        self._check_horizon(tick)
        block = self._block_at_or_before(tick)
        return block is not None and tick < block.end

    def working_prefix(self, tick: Tick) -> int:
        """W(tick): working ticks in ``[0, tick)``."""
        self._check_horizon(tick)
        block = self._block_at_or_before(tick - 1) if tick else None
        if block is None:
            return 0
        return block.work_before + min(tick, block.end) - block.start

    def tick_at_work_index(self, work_index: int) -> Tick:
        """Inverse of W: the tick on which the ``work_index``-th unit is worked."""
        if not 0 <= work_index < self.total_work:
            raise ValueError(f"work index {work_index} outside [0, {self.total_work})")
        position = bisect_right(self._work_marks, work_index) - 1
        block = self._blocks[position]
        return block.start + (work_index - block.work_before)

    def next_working_tick(self, tick: Tick) -> Tick:
        """First working tick at or after ``tick``; the frozen-zone and lag rules
        both resolve forward this way rather than scheduling into a gap."""
        self._check_horizon(tick)
        position = bisect_right(self._starts, tick) - 1
        if position >= 0 and tick < self._blocks[position].end:
            return tick
        following = position + 1
        if following < len(self._blocks):
            return self._blocks[following].start
        raise ValueError(f"no working tick at or after {tick} within the horizon")

    def previous_working_tick(self, tick: Tick) -> Tick | None:
        """Last working tick at or before ``tick``, or ``None`` if there is none.

        The mirror of :meth:`next_working_tick`, and what a backward pass needs:
        a late start pushed into a gap must resolve *backwards* to stay legal,
        where an early start resolves forwards.
        """
        if tick < 0:
            return None
        self._check_horizon(min(tick, self.horizon))
        position = bisect_right(self._starts, min(tick, self.horizon)) - 1
        if position < 0:
            return None
        block = self._blocks[position]
        return min(tick, block.end - 1)

    def latest_start_for_finish(self, finish: Tick, duration: int) -> Tick | None:
        """Latest start whose work fits entirely before ``finish``.

        The backward-pass primitive. ``None`` when the horizon before ``finish``
        cannot hold ``duration`` units at all.
        """
        if duration == 0:
            return self.previous_working_tick(min(finish, self.horizon))
        available = self.working_prefix(max(0, min(finish, self.horizon)))
        if available < duration:
            return None
        return self.tick_at_work_index(available - duration)

    def earliest_start_for_finish(self, finish: Tick, duration: int) -> Tick:
        """Earliest start whose work ends at or after ``finish``.

        The forward-pass primitive for FF and SF links, which bound a
        successor's *finish* and so cannot be applied to its start directly.
        """
        if duration == 0:
            return self.next_working_tick(max(0, min(finish, self.horizon)))
        target = max(0, min(finish - 1, self.horizon))
        last_index = self.working_prefix(self.next_working_tick(target))
        return self.tick_at_work_index(max(0, last_index - duration + 1))

    def span(self, start: Tick, duration: int) -> int:
        """Ticks occupied by ``duration`` units of work beginning at ``start``.

        This is the derived quantity ADR-8 turns on: duration is work content,
        span is what it costs on the axis once non-working time is straddled.
        """
        if duration < 0:
            raise ValueError("duration must be non-negative")
        if duration == 0:
            return 0
        if not self.is_working(start):
            raise ValueError(f"tick {start} is not a working tick; work cannot begin there")
        first = self.working_prefix(start)
        if first + duration > self.total_work:
            raise ValueError(
                f"{duration} units of work from tick {start} exceed the "
                f"{self.total_work - first} remaining in the horizon"
            )
        last_tick = self.tick_at_work_index(first + duration - 1)
        return last_tick + 1 - start

    def finish(self, start: Tick, duration: int) -> Tick:
        """Exclusive end tick of work starting at ``start``."""
        return start + self.span(start, duration)

    def intersect(self, other: CalendarIndex) -> CalendarIndex:
        """Working time common to both — the effective-calendar operation.

        A task progresses only when its own calendar and every assigned
        resource agree, so effective calendars are intersections and the
        representation has to be closed under this.
        """
        if other.axis != self.axis:
            raise ValueError("cannot intersect indexes built on different time axes")
        runs: list[tuple[Tick, Tick]] = []
        i = j = 0
        mine, theirs = self._blocks, other._blocks
        while i < len(mine) and j < len(theirs):
            start = max(mine[i].start, theirs[j].start)
            end = min(mine[i].end, theirs[j].end)
            if start < end:
                runs.append((start, end))
            if mine[i].end < theirs[j].end:
                i += 1
            else:
                j += 1
        return CalendarIndex(_merge_adjacent(runs), self.axis, min(self.horizon, other.horizon))
