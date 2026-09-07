"""Overtime aggregation: thresholds as data, resource-week attribution, no pyramiding."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from planreplan.domain import (
    Assignment,
    Calendar,
    CalendarException,
    DayOfWeek,
    OvertimeResolutionError,
    PayClass,
    PayRules,
    Project,
    Resource,
    Shift,
    Task,
    TimeAxis,
    overtime_report,
    validate_project,
)

NY = ZoneInfo("America/New_York")
AXIS = TimeAxis(epoch=datetime(2026, 9, 7, tzinfo=NY))  # a Monday, midnight
WEEKDAYS = tuple(DayOfWeek)[:5]

EIGHT = (Shift(name="day", start_minute=8 * 60, end_minute=16 * 60),)
TEN = (Shift(name="day", start_minute=7 * 60, end_minute=17 * 60),)
NIGHT = (Shift(name="night", start_minute=22 * 60, end_minute=24 * 60),)

FIVE_BY_EIGHT = Calendar(id="5x8", week_pattern=dict.fromkeys(WEEKDAYS, EIGHT))
FOUR_BY_TEN = Calendar(id="4x10", week_pattern=dict.fromkeys(WEEKDAYS[:4], TEN))
SEVEN_BY_EIGHT = Calendar(id="7x8", week_pattern=dict.fromkeys(DayOfWeek, EIGHT))
NIGHTS = Calendar(id="nights", week_pattern=dict.fromkeys(WEEKDAYS, NIGHT))
#: A short week that never approaches forty hours, for testing positional
#: premiums in isolation from the weekly threshold.
FRI_SAT = Calendar(
    id="fri_sat",
    week_pattern={DayOfWeek.FRIDAY: EIGHT, DayOfWeek.SATURDAY: EIGHT},
)

ALL_CALENDARS = (FIVE_BY_EIGHT, FOUR_BY_TEN, SEVEN_BY_EIGHT, NIGHTS, FRI_SAT)


def build(
    *,
    duration: int,
    calendar_id: str = "5x8",
    rules: PayRules | None = None,
    resource_calendar: str | None = None,
    resource_exceptions: tuple[CalendarException, ...] = (),
    axis: TimeAxis = AXIS,
    calendars=ALL_CALENDARS,
):
    project = Project(
        id="p",
        axis=axis,
        calendars=calendars,
        default_calendar_id=calendar_id,
        pay_rules=(rules,) if rules else (),
        tasks=(Task(id="t", duration=duration),),
        resources=(
            Resource(
                id="crew",
                capacity=4,
                calendar_id=resource_calendar,
                pay_rules_id=rules.id if rules else None,
                exceptions=resource_exceptions,
            ),
        ),
        assignments=(Assignment(task_id="t", resource_id="crew"),),
    )
    index = validate_project(project)
    calendar = index.effective_calendar("t")
    start = calendar.tick_at_work_index(0)
    return index, {"t": (start, calendar.finish(start, duration))}


def tally(reports, pay_class: PayClass) -> int:
    return sum(r.premium_ticks.get(pay_class, 0) for r in reports)


# -- the roadmap's 4x10 acceptance case ------------------------------------


def test_a_four_by_ten_week_accrues_no_weekly_overtime():
    index, spans = build(duration=40, calendar_id="4x10")
    reports = overtime_report(index, spans)
    assert tally(reports, PayClass.WEEKLY_OVERTIME) == 0


def test_a_four_by_ten_week_accrues_daily_overtime_unless_exempted():
    """Whether a compressed week is exempt is data, never a hardcoded 8/40."""
    charged = PayRules(id="strict", daily_regular_hours=8, weekly_regular_hours=40)
    exempt = PayRules(id="exempt", daily_regular_hours=10, weekly_regular_hours=40)

    index, spans = build(duration=40, calendar_id="4x10", rules=charged)
    assert tally(overtime_report(index, spans), PayClass.DAILY_OVERTIME) == 8

    index, spans = build(duration=40, calendar_id="4x10", rules=exempt)
    assert tally(overtime_report(index, spans), PayClass.DAILY_OVERTIME) == 0


def test_a_forty_hour_five_by_eight_week_is_all_regular():
    index, spans = build(duration=40)
    reports = overtime_report(index, spans)
    assert len(reports) == 1
    assert reports[0].regular_ticks == 40
    assert reports[0].premium_ticks == {}


def test_the_forty_first_hour_is_weekly_overtime():
    index, spans = build(duration=48, calendar_id="7x8")
    reports = overtime_report(index, spans)
    week = reports[0]
    assert week.regular_ticks == 40
    assert week.premium_ticks[PayClass.WEEKLY_OVERTIME] == 8


# -- positional premiums are independent of thresholds ---------------------


def test_a_saturday_carries_its_premium_on_a_short_week():
    """Positional premium applies whether or not the week reached forty hours."""
    rules = PayRules(id="cba", day_premiums={DayOfWeek.SATURDAY: 1.5})
    index, spans = build(duration=16, calendar_id="fri_sat", rules=rules)
    reports = overtime_report(index, spans)
    assert reports[0].total_ticks == 16  # nowhere near forty
    assert reports[0].regular_ticks == 8  # Friday
    assert tally(reports, PayClass.DAY_PREMIUM) == 8  # Saturday
    assert tally(reports, PayClass.WEEKLY_OVERTIME) == 0


def test_a_shift_premium_is_keyed_by_shift_name():
    rules = PayRules(id="cba", shift_premiums={"night": 1.25})
    index, spans = build(duration=8, calendar_id="nights", rules=rules)
    assert tally(overtime_report(index, spans), PayClass.SHIFT_PREMIUM) == 8


def test_working_a_holiday_earns_the_holiday_premium():
    """A holiday is normally non-working; a callout on one is the case that matters."""
    callout = CalendarException(
        start_date=date(2026, 9, 9),
        end_date=date(2026, 9, 9),
        working=True,
        holiday=True,
        shifts=EIGHT,
        reason="holiday callout",
    )
    holidayed = FIVE_BY_EIGHT.model_copy(update={"exceptions": (callout,)})
    calendars = (holidayed, *ALL_CALENDARS[1:])
    rules = PayRules(id="cba", holiday_premium=2.0)
    index, spans = build(duration=24, rules=rules, calendars=calendars)
    assert tally(overtime_report(index, spans), PayClass.HOLIDAY) == 8


# -- no pyramiding ---------------------------------------------------------


def test_an_hour_earns_one_class_at_the_highest_multiplier():
    """Sunday at double beats weekly overtime at time-and-a-half, without
    special-casing: the comparison is on the multipliers themselves."""
    rules = PayRules(
        id="cba",
        day_premiums={DayOfWeek.SUNDAY: 2.0},
        weekly_overtime_multiplier=1.5,
    )
    index, spans = build(duration=56, calendar_id="7x8", rules=rules)
    reports = overtime_report(index, spans)
    sunday = tally(reports, PayClass.DAY_PREMIUM)
    assert sunday == 8
    assert sum(r.total_ticks for r in reports) == 56


def test_regular_plus_premium_always_equals_total():
    """The invariant that a report counting an hour twice could not hold."""
    rules = PayRules(
        id="cba",
        day_premiums={DayOfWeek.SATURDAY: 1.5, DayOfWeek.SUNDAY: 2.0},
        shift_premiums={"day": 1.1},
    )
    index, spans = build(duration=60, calendar_id="7x8", rules=rules)
    reports = overtime_report(index, spans)
    assert sum(r.total_ticks for r in reports) == 60
    for report in reports:
        assert report.total_ticks == report.regular_ticks + sum(report.premium_ticks.values())


# -- resource-week attribution ---------------------------------------------


def test_weekly_overtime_comes_from_the_combination_of_assignments():
    """The point of aggregating by resource: neither task exceeds forty alone."""
    project = Project(
        id="p",
        axis=AXIS,
        calendars=ALL_CALENDARS,
        default_calendar_id="7x8",
        tasks=(Task(id="a", duration=24), Task(id="b", duration=24)),
        resources=(Resource(id="crew", capacity=4),),
        assignments=(
            Assignment(task_id="a", resource_id="crew"),
            Assignment(task_id="b", resource_id="crew"),
        ),
    )
    index = validate_project(project)
    calendar = index.effective_calendar("a")
    a_start = calendar.tick_at_work_index(0)
    b_start = calendar.tick_at_work_index(24)
    spans = {
        "a": (a_start, calendar.finish(a_start, 24)),
        "b": (b_start, calendar.finish(b_start, 24)),
    }
    reports = overtime_report(index, spans)
    assert sum(r.total_ticks for r in reports) == 48
    assert tally(reports, PayClass.WEEKLY_OVERTIME) == 8


def test_concurrent_assignments_do_not_invent_hours():
    """One crew on two simultaneous tasks worked one shift, not two."""
    project = Project(
        id="p",
        axis=AXIS,
        calendars=ALL_CALENDARS,
        default_calendar_id="5x8",
        tasks=(Task(id="a", duration=8), Task(id="b", duration=8)),
        resources=(Resource(id="crew", capacity=4),),
        assignments=(
            Assignment(task_id="a", resource_id="crew", demand=2),
            Assignment(task_id="b", resource_id="crew", demand=2),
        ),
    )
    index = validate_project(project)
    calendar = index.effective_calendar("a")
    start = calendar.tick_at_work_index(0)
    span = (start, calendar.finish(start, 8))
    reports = overtime_report(index, {"a": span, "b": span})
    assert sum(r.total_ticks for r in reports) == 8


def test_a_resource_with_no_assignments_produces_no_rows():
    index, spans = build(duration=8)
    assert all(r.resource_id == "crew" for r in overtime_report(index, spans))


def test_idle_time_inside_a_span_is_not_paid():
    """The canonical Friday scenario: reserved across the weekend, not paid for it.

    Eight hours begun at 13:00 Friday occupy 72 ticks on the axis. Sixty-four
    of those are the weekend, and nobody is paid for them.
    """
    index, _ = build(duration=8)
    calendar = index.effective_calendar("t")
    friday_1pm = 4 * 24 + 13
    span = (friday_1pm, calendar.finish(friday_1pm, 8))
    assert span[1] - span[0] == 72
    reports = overtime_report(index, {"t": span})
    assert sum(r.total_ticks for r in reports) == 8


# -- resolution ------------------------------------------------------------


def test_an_axis_that_cannot_express_an_hour_is_refused():
    daily = TimeAxis(epoch=datetime(2026, 9, 7, tzinfo=NY), ticks_per_day=1)
    project = Project(
        id="p",
        axis=daily,
        calendars=(
            Calendar(
                id="c",
                week_pattern=dict.fromkeys(WEEKDAYS, (Shift(start_minute=0, end_minute=1440),)),
            ),
        ),
        default_calendar_id="c",
        tasks=(Task(id="t", duration=2),),
        resources=(Resource(id="crew", capacity=1),),
        assignments=(Assignment(task_id="t", resource_id="crew"),),
    )
    index = validate_project(project)
    with pytest.raises(OvertimeResolutionError, match="whole number of"):
        overtime_report(index, {"t": (0, 2)})


def test_half_hour_resolution_scales_the_thresholds():
    fine = TimeAxis(epoch=datetime(2026, 9, 7, tzinfo=NY), ticks_per_day=48)
    index, spans = build(duration=96, calendar_id="7x8", axis=fine)
    reports = overtime_report(index, spans)
    assert reports[0].regular_ticks == 80  # forty hours at two ticks each
    assert reports[0].premium_ticks[PayClass.WEEKLY_OVERTIME] == 16
