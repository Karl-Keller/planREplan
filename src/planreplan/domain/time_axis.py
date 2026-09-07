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
from typing import Any, Final
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, computed_field, field_validator, model_validator

#: An integer tick offset from a project epoch. Kept as a plain alias so the
#: type is documentation, not a wrapper the solver would have to unwrap.
Tick = int

SECONDS_PER_DAY: Final = 86_400
SECONDS_PER_HOUR: Final = 3_600


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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def timezone(self) -> str:
        """IANA zone name, persisted alongside the epoch.

        An ISO-8601 timestamp carries only a UTC *offset*, which is not enough
        to reconstruct a zone: ``-05:00`` cannot say whether the zone observes
        daylight saving. Reloading from the offset alone silently turns every
        local shift boundary after the next transition by an hour. The name is
        therefore part of the serialised form, per the persistence rules in
        ``docs/03-domain-model.md``.
        """
        return str(self.epoch.tzinfo)

    @model_validator(mode="before")
    @classmethod
    def _restore_zone(cls, data: Any) -> Any:  # noqa: ANN401 — pre-validators take raw input
        """Re-attach the named zone when loading a serialised axis."""
        if not isinstance(data, dict) or "timezone" not in data:
            return data
        restored = {key: value for key, value in data.items() if key != "timezone"}
        name = data["timezone"]
        epoch = restored.get("epoch")
        if isinstance(epoch, str):
            epoch = datetime.fromisoformat(epoch)
        if isinstance(epoch, datetime) and epoch.tzinfo is not None and name:
            restored["epoch"] = epoch.astimezone(ZoneInfo(name))
        return restored

    @field_validator("epoch")
    @classmethod
    def _epoch_must_be_timezone_aware(cls, value: datetime) -> datetime:
        """A naive epoch cannot absorb DST, which is the axis's one hard job."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("epoch must be timezone-aware (see ADR-8)")
        return value

    @model_validator(mode="after")
    def _resolution_must_survive_daylight_saving(self) -> TimeAxis:
        """A tick must divide an hour wherever the clock changes.

        Ticks are absolute and uniform, which is the point of ADR-8. Local days
        are not: across a transition one is 23 hours and another 25. So local
        midnight only keeps landing on the tick grid when a tick divides an
        hour — true at 24, 48, or 96 ticks per day, false at 8 or 1.

        Coarser resolutions remain available in a zone with a fixed offset,
        which is the honest trade: uniform physical ticks, or local days that
        occasionally are not days. Refusing here turns what would otherwise be
        an obscure alignment failure six months into a project's calendar into
        a message at the moment the axis is defined.
        """
        if SECONDS_PER_HOUR % self.seconds_per_tick == 0 or not self._observes_daylight_saving():
            return self
        raise ValueError(
            f"ticks_per_day={self.ticks_per_day} gives a tick of {self.seconds_per_tick}s, "
            f"which does not divide an hour, and {self.epoch.tzinfo} changes its clock. "
            "Use 24, 48, or 96 ticks per day in a daylight-saving zone, or a "
            "fixed-offset zone such as UTC for coarser resolutions."
        )

    def _observes_daylight_saving(self) -> bool:
        """Whether the epoch's zone shifts its offset during the epoch's year."""
        offsets = {
            self.epoch.replace(month=month, day=1, hour=12).utcoffset() for month in (1, 4, 7, 10)
        }
        return len(offsets) > 1

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
