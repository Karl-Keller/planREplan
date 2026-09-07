"""Sizing a project's horizon to an actual schedule.

Validation sizes the horizon to the network's longest chain, which is what CPM
needs and no more. A resource-feasible schedule runs past that chain — often
far past it, since contention is exactly what CPM ignores — so anything that
schedules has to widen the horizon first.

Doing it from a *real* schedule rather than a formula is the point. The obvious
analytic bounds are either far too loose (the sequential sum of every task,
which was the previous behaviour and cost seconds of calendar building) or not
actually bounds at all: on the 500-task benchmark the chain is 503 working
ticks and the greedy makespan is 2534, so anything derived from the chain alone
would have been too small.
"""

from __future__ import annotations

from planreplan.domain.entities import Project
from planreplan.domain.schedule import Schedule
from planreplan.domain.time_axis import Tick
from planreplan.domain.validation import ProjectIndex, validate_project
from planreplan.solve.greedy import GreedyError, greedy_schedule

#: Room beyond the greedy makespan. A solver may legitimately place a task
#: later than greedy did while still improving the objective.
_HEADROOM = 2

#: Doublings tried before giving up. Each is a cheap greedy run plus a
#: validation at a horizon still far below the old sequential bound.
_MAX_ATTEMPTS = 8


def fitted_index(index: ProjectIndex, *, data_date: Tick = 0) -> tuple[ProjectIndex, Schedule]:
    """Re-validate ``index``'s project at a horizon sized to a real schedule.

    Returns the widened index and the greedy schedule that sized it, since the
    caller invariably wants both: the schedule is also the solver's hint and
    its fallback.

    A project carrying an explicit ``planningHorizon`` is left alone — the
    maintainer said what they meant, and silently widening it would make the
    field advisory.
    """
    if index.project.planning_horizon is not None:
        return index, greedy_schedule(index, data_date=data_date)

    current = index
    horizon = index.horizon
    for _ in range(_MAX_ATTEMPTS):
        try:
            schedule = greedy_schedule(current, data_date=data_date)
        except GreedyError:
            horizon *= 2
            current = _revalidated(index.project, horizon)
            continue
        wanted = min(schedule.project_finish * _HEADROOM, horizon * _HEADROOM)
        if wanted <= current.horizon:
            return current, schedule
        return _revalidated(index.project, wanted), schedule
    raise GreedyError(
        "no serial placement fits after repeated widening; set planning_horizon "
        "explicitly or relax a resource capacity"
    )


def _revalidated(project: Project, horizon: Tick) -> ProjectIndex:
    return validate_project(project.model_copy(update={"planning_horizon": horizon}))
