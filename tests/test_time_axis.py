"""The tick axis: absolute arithmetic, and refusal to round shift boundaries."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from planreplan.domain import TimeAxis

NY = ZoneInfo("America/New_York")
EPOCH = datetime(2026, 9, 7, tzinfo=NY)  # a Monday, midnight local


def test_epoch_must_be_timezone_aware():
    with pytest.raises(ValueError, match="timezone-aware"):
        TimeAxis(epoch=datetime(2026, 9, 7))


def test_ticks_per_day_must_divide_a_day():
    with pytest.raises(ValueError, match="divide a day"):
        TimeAxis(epoch=EPOCH, ticks_per_day=7)


@pytest.mark.parametrize(("per_day", "seconds"), [(1, 86_400), (8, 10_800), (24, 3_600), (96, 900)])
def test_resolutions_give_whole_second_ticks(per_day, seconds):
    assert TimeAxis(epoch=EPOCH, ticks_per_day=per_day).seconds_per_tick == seconds


def test_tick_arithmetic_is_absolute_across_a_dst_transition():
    """The reason conversions route through UTC.

    US DST begins 02:00 on 2026-03-08, so 48 hourly ticks from Saturday
    midnight span a day that is only 23 hours long. Absolute arithmetic lands
    at 01:00 on Monday; naive wall-clock addition would claim midnight, having
    silently invented the hour that went missing.
    """
    axis = TimeAxis(epoch=datetime(2026, 3, 7, tzinfo=NY))  # day before spring forward
    assert axis.to_datetime(24) == datetime(2026, 3, 8, tzinfo=NY)  # transition not yet reached
    assert axis.to_datetime(48) == datetime(2026, 3, 9, 1, tzinfo=NY)
    assert axis.to_datetime(48) != axis.epoch + timedelta(hours=48)


def test_a_fall_back_day_is_twenty_five_ticks_long():
    """The mirror case: DST ends 02:00 on 2026-11-01, so that day gains an hour."""
    axis = TimeAxis(epoch=datetime(2026, 10, 31, tzinfo=NY))
    assert axis.to_datetime(24) == datetime(2026, 11, 1, tzinfo=NY)
    assert axis.to_datetime(24 + 25) == datetime(2026, 11, 2, tzinfo=NY)


def test_to_tick_exact_rejects_an_unaligned_boundary():
    """A 06:30 start on an hourly axis is a modelling error, not a rounding job."""
    axis = TimeAxis(epoch=EPOCH, ticks_per_day=24)
    with pytest.raises(ValueError, match="does not align"):
        axis.to_tick_exact(datetime(2026, 9, 7, 6, 30, tzinfo=NY))


def test_a_finer_axis_accepts_the_same_boundary():
    axis = TimeAxis(epoch=EPOCH, ticks_per_day=48)
    assert axis.to_tick_exact(datetime(2026, 9, 7, 6, 30, tzinfo=NY)) == 13


def test_to_tick_floors_while_to_tick_exact_refuses():
    axis = TimeAxis(epoch=EPOCH, ticks_per_day=24)
    moment = datetime(2026, 9, 7, 6, 30, tzinfo=NY)
    assert axis.to_tick(moment) == 6


def test_naive_moments_are_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        TimeAxis(epoch=EPOCH).to_tick(datetime(2026, 9, 8))


@given(tick=st.integers(min_value=-10_000, max_value=100_000), per_day=st.sampled_from([1, 24, 96]))
def test_datetime_and_tick_round_trip(tick, per_day):
    axis = TimeAxis(epoch=EPOCH, ticks_per_day=per_day)
    assert axis.to_tick_exact(axis.to_datetime(tick)) == tick


@given(tick=st.integers(min_value=0, max_value=50_000))
def test_ticks_are_equal_physical_durations(tick):
    """Every tick is the same number of seconds, DST notwithstanding."""
    axis = TimeAxis(epoch=EPOCH)
    a = axis.to_datetime(tick).astimezone(UTC)
    b = axis.to_datetime(tick + 1).astimezone(UTC)
    assert (b - a).total_seconds() == axis.seconds_per_tick
