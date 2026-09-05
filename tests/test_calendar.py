"""Calendars as predicates over the tick axis: spans, DST, and intersection."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from planreplan.domain import (
    Calendar,
    CalendarException,
    CalendarIndex,
    DayOfWeek,
    Shift,
    TimeAxis,
)

NY = ZoneInfo("America/New_York")
EPOCH = datetime(2026, 9, 7, tzinfo=NY)  # a Monday, midnight local
HORIZON = 24 * 60

DAY_SHIFT = (Shift(name="day", start_minute=8 * 60, end_minute=16 * 60),)
CONTINUOUS = (Shift(name="continuous", start_minute=0, end_minute=1440),)
WEEKDAYS = tuple(DayOfWeek)[:5]

FRIDAY_1PM = 4 * 24 + 13
FRIDAY_8AM = 4 * 24 + 8

#: The horizon spans 60 days from a Monday: eight whole weeks plus Mon-Thu.
WORKING_DAYS = 8 * 5 + 4


def axis(ticks_per_day: int = 24) -> TimeAxis:
    return TimeAxis(epoch=EPOCH, ticks_per_day=ticks_per_day)


def index(calendar: Calendar, ticks_per_day: int = 24, horizon: int = HORIZON) -> CalendarIndex:
    return CalendarIndex.build(calendar, axis(ticks_per_day), horizon)


FIVE_BY_EIGHT = Calendar(id="5x8", week_pattern=dict.fromkeys(WEEKDAYS, DAY_SHIFT))
SEVEN_BY_EIGHT = Calendar(id="7x8", week_pattern=dict.fromkeys(DayOfWeek, DAY_SHIFT))
SEVEN_BY_24 = Calendar(id="7x24", week_pattern=dict.fromkeys(DayOfWeek, CONTINUOUS))
FOUR_BY_TEN = Calendar(
    id="4x10",
    week_pattern=dict.fromkeys(
        WEEKDAYS[:4], (Shift(name="long", start_minute=7 * 60, end_minute=17 * 60),)
    ),
)


# --------------------------------------------------------------------------
# The worked example published in docs/03-domain-model.md
# --------------------------------------------------------------------------


def test_the_friday_afternoon_scenario_from_the_design_docs():
    """Eight hours of work begun at 13:00 Friday, finishing Monday at 13:00.

    Three working ticks before the shift ends, sixty-four idle over the
    weekend, five on Monday: duration 8, span 72. This is the case that
    motivated the uniform axis, so it is pinned to exact numbers.
    """
    cal = index(FIVE_BY_EIGHT)
    assert cal.span(FRIDAY_1PM, 8) == 72
    assert cal.finish(FRIDAY_1PM, 8) == 181
    assert cal.axis.to_datetime(181) == datetime(2026, 9, 14, 13, tzinfo=NY)
    assert cal.working_prefix(181) - cal.working_prefix(FRIDAY_1PM) == 8


def test_the_same_duration_costs_less_on_a_seven_day_calendar():
    """Duration is work content; span is what the calendar charges for it."""
    assert index(SEVEN_BY_EIGHT).span(FRIDAY_1PM, 8) == 8 + 16  # one overnight gap
    assert index(SEVEN_BY_24).span(FRIDAY_1PM, 8) == 8  # no gap at all


def test_twenty_four_hours_of_work_across_calendar_shapes():
    assert index(SEVEN_BY_24).span(FRIDAY_8AM, 24) == 24
    assert index(FIVE_BY_EIGHT).span(FRIDAY_8AM, 24) == 104


def test_a_three_day_hot_fix_started_friday_runs_through_the_weekend():
    """The scenario that ruled out a compressed working-time axis."""
    seven = index(SEVEN_BY_24)
    start = seven.axis.to_tick_exact(datetime(2026, 9, 11, tzinfo=NY))  # Friday midnight
    assert seven.span(start, 72) == 72
    assert seven.axis.to_datetime(seven.finish(start, 72)) == datetime(2026, 9, 14, tzinfo=NY)


# --------------------------------------------------------------------------
# Working-time queries
# --------------------------------------------------------------------------


def test_adjacent_runs_collapse_into_one_block():
    """A continuous calendar is one block; a gapped one keeps a block per shift."""
    assert len(index(SEVEN_BY_24).blocks) == 1
    five = index(FIVE_BY_EIGHT)
    assert len(five.blocks) == WORKING_DAYS
    assert five.total_work == WORKING_DAYS * 8


def test_working_prefix_ignores_idle_time():
    cal = index(FIVE_BY_EIGHT)
    friday_close = 4 * 24 + 16
    monday_open = 7 * 24 + 8
    assert cal.working_prefix(friday_close) == cal.working_prefix(monday_open)


def test_is_working_tracks_the_shift_boundaries():
    cal = index(FIVE_BY_EIGHT)
    assert not cal.is_working(7)  # Monday 07:00
    assert cal.is_working(8)  # Monday 08:00
    assert cal.is_working(15)  # Monday 15:00, last hour worked
    assert not cal.is_working(16)  # Monday 16:00, end is exclusive
    assert not cal.is_working(5 * 24 + 10)  # Saturday


def test_next_working_tick_resolves_forward_out_of_a_gap():
    cal = index(FIVE_BY_EIGHT)
    saturday_noon = 5 * 24 + 12
    assert cal.next_working_tick(saturday_noon) == 7 * 24 + 8
    assert cal.next_working_tick(8) == 8  # already working


def test_work_index_inverts_the_prefix_function():
    cal = index(FIVE_BY_EIGHT)
    for work_index in (0, 1, 7, 8, 40, 100):
        tick = cal.tick_at_work_index(work_index)
        assert cal.is_working(tick)
        assert cal.working_prefix(tick) == work_index


def test_queries_beyond_the_horizon_are_refused_not_answered_falsely():
    """Silently reporting non-working time would look like a project that stops."""
    cal = index(FIVE_BY_EIGHT)
    with pytest.raises(ValueError, match="outside the indexed horizon"):
        cal.is_working(HORIZON + 1)


def test_work_that_overruns_the_horizon_is_refused():
    cal = index(FIVE_BY_EIGHT, horizon=48)
    with pytest.raises(ValueError, match="exceed"):
        cal.span(8, 500)


def test_work_cannot_begin_on_a_non_working_tick():
    with pytest.raises(ValueError, match="not a working tick"):
        index(FIVE_BY_EIGHT).span(5 * 24, 8)  # Saturday midnight


def test_zero_duration_milestones_occupy_no_span():
    assert index(FIVE_BY_EIGHT).span(8, 0) == 0


# --------------------------------------------------------------------------
# Daylight saving
# --------------------------------------------------------------------------


def test_a_wall_clock_shift_shortens_on_a_spring_forward_day():
    """A crew is on site for the hours that physically exist.

    DST begins 02:00 on 2026-03-08. A midnight-to-08:00 shift is eight hours
    of wall clock but seven hours of work, and ticks measure the latter.
    """
    march = TimeAxis(epoch=datetime(2026, 3, 1, tzinfo=NY))
    night = Calendar(
        id="night", week_pattern=dict.fromkeys(DayOfWeek, (Shift(start_minute=0, end_minute=480),))
    )
    cal = CalendarIndex.build(night, march, 24 * 20)
    transition_day = march.to_tick_exact(datetime(2026, 3, 8, tzinfo=NY))
    next_day = march.to_tick_exact(datetime(2026, 3, 9, tzinfo=NY))
    assert cal.working_prefix(next_day) - cal.working_prefix(transition_day) == 7


def test_a_wall_clock_shift_lengthens_on_a_fall_back_day():
    """DST ends 02:00 on 2026-11-01; the same shift is nine hours of work."""
    november = TimeAxis(epoch=datetime(2026, 10, 25, tzinfo=NY))
    night = Calendar(
        id="night", week_pattern=dict.fromkeys(DayOfWeek, (Shift(start_minute=0, end_minute=480),))
    )
    cal = CalendarIndex.build(night, november, 24 * 20)
    transition_day = november.to_tick_exact(datetime(2026, 11, 1, tzinfo=NY))
    next_day = november.to_tick_exact(datetime(2026, 11, 2, tzinfo=NY))
    assert cal.working_prefix(next_day) - cal.working_prefix(transition_day) == 9


# --------------------------------------------------------------------------
# Exceptions and effective calendars
# --------------------------------------------------------------------------


def test_a_holiday_removes_a_day_and_pushes_the_span():
    holiday = CalendarException(
        start_date=date(2026, 9, 9), end_date=date(2026, 9, 9), reason="holiday"
    )
    plain = index(FIVE_BY_EIGHT)
    with_holiday = index(FIVE_BY_EIGHT.model_copy(update={"exceptions": (holiday,)}))
    monday_8am = 8
    assert with_holiday.span(monday_8am, 24) == plain.span(monday_8am, 24) + 24


def test_a_working_exception_opens_a_normally_closed_day():
    """Saturday callout: one mechanism covers shutdowns and additions alike."""
    callout = CalendarException(
        start_date=date(2026, 9, 12),
        end_date=date(2026, 9, 12),
        working=True,
        shifts=DAY_SHIFT,
        reason="Saturday callout",
    )
    cal = index(FIVE_BY_EIGHT.model_copy(update={"exceptions": (callout,)}))
    assert cal.is_working(5 * 24 + 9)  # Saturday 09:00


def test_the_last_matching_exception_wins():
    shutdown = CalendarException(
        start_date=date(2026, 9, 7), end_date=date(2026, 9, 11), reason="site shutdown"
    )
    callout = CalendarException(
        start_date=date(2026, 9, 9),
        end_date=date(2026, 9, 9),
        working=True,
        shifts=DAY_SHIFT,
        reason="crew recalled",
    )
    cal = index(FIVE_BY_EIGHT.model_copy(update={"exceptions": (shutdown, callout)}))
    assert not cal.is_working(9)  # Monday, shut down
    assert cal.is_working(2 * 24 + 9)  # Wednesday, recalled


def test_intersection_is_the_effective_calendar():
    """Work happens only when the task and every assigned resource agree."""
    effective = index(SEVEN_BY_24).intersect(index(FIVE_BY_EIGHT))
    assert effective.blocks == index(FIVE_BY_EIGHT).blocks


def test_intersection_of_disjoint_calendars_is_empty():
    morning = Calendar(
        id="am", week_pattern=dict.fromkeys(DayOfWeek, (Shift(start_minute=0, end_minute=480),))
    )
    evening = Calendar(
        id="pm", week_pattern=dict.fromkeys(DayOfWeek, (Shift(start_minute=960, end_minute=1440),))
    )
    assert index(morning).intersect(index(evening)).total_work == 0


def test_intersecting_indexes_from_different_axes_is_refused():
    other = CalendarIndex.build(FIVE_BY_EIGHT, axis(ticks_per_day=48), HORIZON)
    with pytest.raises(ValueError, match="different time axes"):
        index(FIVE_BY_EIGHT).intersect(other)


# --------------------------------------------------------------------------
# Shift and alignment validation
# --------------------------------------------------------------------------


def test_a_shift_must_end_after_it_starts():
    with pytest.raises(ValueError, match="not after"):
        Shift(start_minute=960, end_minute=480)


def test_overlapping_shifts_in_a_day_are_rejected():
    with pytest.raises(ValueError, match="overlap or are unordered"):
        Calendar(
            id="bad",
            week_pattern={
                DayOfWeek.MONDAY: (
                    Shift(name="a", start_minute=480, end_minute=720),
                    Shift(name="b", start_minute=600, end_minute=900),
                )
            },
        )


def test_a_non_working_exception_cannot_carry_shifts():
    with pytest.raises(ValueError, match="cannot carry shifts"):
        CalendarException(start_date=date(2026, 9, 9), end_date=date(2026, 9, 9), shifts=DAY_SHIFT)


def test_a_half_past_shift_needs_a_finer_axis():
    """06:30 starts are ordinary in construction; the axis must be able to say so."""
    early = Calendar(
        id="early",
        week_pattern=dict.fromkeys(WEEKDAYS, (Shift(start_minute=390, end_minute=930),)),
    )
    with pytest.raises(ValueError, match="does not align"):
        index(early, ticks_per_day=24)
    fine = index(early, ticks_per_day=48, horizon=48 * 60)
    assert fine.total_work == WORKING_DAYS * 18  # 06:30-15:30 is 18 half-hour ticks


def test_a_four_by_ten_week_yields_forty_hours():
    cal = index(FOUR_BY_TEN)
    week = cal.working_prefix(7 * 24) - cal.working_prefix(0)
    assert week == 40


# --------------------------------------------------------------------------
# Properties
# --------------------------------------------------------------------------

shifts = st.builds(
    lambda pair: Shift(start_minute=pair[0] * 60, end_minute=pair[1] * 60),
    st.lists(st.integers(0, 24), min_size=2, max_size=2, unique=True).map(sorted).map(tuple),
)
calendars = st.builds(
    lambda pattern: Calendar(id="generated", week_pattern=pattern),
    st.dictionaries(
        st.sampled_from(list(DayOfWeek)),
        shifts.map(lambda s: (s,)),
        min_size=1,
    ),
)


@given(calendar=calendars, tick=st.integers(0, HORIZON))
@settings(max_examples=50, deadline=None)
def test_working_prefix_is_monotone_and_bounded(calendar, tick):
    cal = index(calendar)
    assert cal.working_prefix(0) == 0
    assert 0 <= cal.working_prefix(tick) <= tick
    if tick > 0:
        assert cal.working_prefix(tick - 1) <= cal.working_prefix(tick)


@given(calendar=calendars, duration=st.integers(1, 30))
@settings(max_examples=50, deadline=None)
def test_span_never_undercounts_the_work_it_contains(calendar, duration):
    """span >= duration always, with equality exactly when no gap is straddled."""
    cal = index(calendar)
    if cal.total_work <= duration:
        return
    start = cal.tick_at_work_index(0)
    span = cal.span(start, duration)
    assert span >= duration
    assert cal.is_working(start)
    assert cal.is_working(start + span - 1)
    assert cal.working_prefix(start + span) - cal.working_prefix(start) == duration


@given(calendar=calendars)
@settings(max_examples=50, deadline=None)
def test_intersection_is_idempotent_and_commutative(calendar):
    cal = index(calendar)
    other = index(FIVE_BY_EIGHT)
    assert cal.intersect(cal).blocks == cal.blocks
    assert cal.intersect(other).blocks == other.intersect(cal).blocks
    assert cal.intersect(other).total_work <= min(cal.total_work, other.total_work)
