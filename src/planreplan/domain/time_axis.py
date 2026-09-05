"""The uniform tick axis (ADR-8).

Every temporal value in the core is an integer tick measured from a project
epoch. The axis is uniform and calendar-agnostic: every tick exists whether or
not anyone is working, which is what makes non-working time *nameable* and so
makes overtime expressible at all. Calendars are predicates over this axis
(see :mod:`planreplan.domain.calendar`), never the axis itself.

Tick arithmetic is absolute, not wall-clock. Adding a ``timedelta`` to an aware
datetime performs wall-clock arithmetic within its zone, which silently gains
or loses an hour across a daylight-saving transition; every conversion here
goes through UTC so that a tick is always the same physical duration.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from pydantic import BaseModel, ConfigDict, field_validator

#: An integer tick offset from a project epoch. Kept as a plain alias so the
#: type is documentation, not a wrapper the solver would have to unwrap.
Tick = int

SECONDS_PER_DAY: Final = 86_400


class TimeAxis(BaseModel):
    """Origin and resolution of a project's tick axis.

    ``ticks_per_day`` is the tuning knob ADR-8 leaves open: 24 for hourly
    (the default), 8 for shift-level, 1 for daily, 96 for quarter-hour. It is
    also the documented escape hatch if a large instance will not solve at
    hourly resolution.
    """

    model_config = ConfigDict(frozen=True)

    epoch: datetime
    ticks_per_day: int = 24

    @field_validator("epoch")
    @classmethod
    def _epoch_must_be_timezone_aware(cls, value: datetime) -> datetime:
        """A naive epoch cannot absorb DST, which is the axis's one hard job."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("epoch must be timezone-aware (see ADR-8)")
        return value

    @field_validator("ticks_per_day")
    @classmethod
    def _ticks_must_divide_a_day(cls, value: int) -> int:
        if value < 1:
            raise ValueError("ticks_per_day must be positive")
        if SECONDS_PER_DAY % value:
            raise ValueError(
                f"ticks_per_day={value} does not divide a day evenly; "
                "a tick must be a whole number of seconds"
            )
        return value

    @property
    def seconds_per_tick(self) -> int:
        """Physical length of one tick. Integer by construction of the validator."""
        return SECONDS_PER_DAY // self.ticks_per_day

    @property
    def minutes_per_tick(self) -> float:
        return self.seconds_per_tick / 60

    def to_datetime(self, tick: Tick) -> datetime:
        """Absolute moment of a tick, expressed in the epoch's zone."""
        moment = self.epoch.astimezone(UTC) + timedelta(seconds=tick * self.seconds_per_tick)
        return moment.astimezone(self.epoch.tzinfo)

    def to_tick(self, moment: datetime) -> Tick:
        """Tick containing ``moment``, rounding down.

        Used at the io boundary, where an arbitrary timestamp lands wherever it
        lands. Shift boundaries use :meth:`to_tick_exact` instead, because a
        boundary that falls between ticks is a modelling error rather than
        something to round away.
        """
        return self._offset_seconds(moment) // self.seconds_per_tick

    def to_tick_exact(self, moment: datetime) -> Tick:
        """Tick of ``moment``, refusing to round.

        Raises:
            ValueError: if ``moment`` does not land on a tick boundary — for
                instance a 06:30 shift start on an hourly axis, which needs
                ``ticks_per_day=48`` rather than a silently shifted schedule.
        """
        offset = self._offset_seconds(moment)
        tick, remainder = divmod(offset, self.seconds_per_tick)
        if remainder:
            raise ValueError(
                f"{moment.isoformat()} does not align to a tick boundary at "
                f"ticks_per_day={self.ticks_per_day}; it falls {remainder}s into the tick"
            )
        return tick

    def _offset_seconds(self, moment: datetime) -> int:
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("moment must be timezone-aware")
        delta = moment.astimezone(UTC) - self.epoch.astimezone(UTC)
        return int(delta.total_seconds())
