"""A deterministic serial schedule, used three ways.

CP-SAT needs a bound, a hint, and a fallback, and one cheap feasible schedule
supplies all three:

* **Bound.** Variable domains and span tables run to this makespan rather than
  the planning horizon, which is sized for calendars, not for schedules. On a
  500-task instance that is the difference between a few hundred rows per task
  and several thousand.
* **Hint.** Handed to the solver as a starting point.
* **Fallback.** When the time limit expires before CP-SAT proves anything, this
  is a real schedule to return instead of a shrug.

The rule is serial: take tasks in topological order, break ties by least float,
place each at the earliest tick where precedence, calendar, and capacity all
allow it. Every dependency kind is a lower bound on the successor, so pushing a
task later to fit a crew can never break a link that was already satisfied.
"""

from __future__ import annotations

from planreplan.cpm import analyse, earliest_dates, topological_order
from planreplan.domain.entities import Dependency
from planreplan.domain.schedule import EntryState, Schedule, ScheduleEntry
from planreplan.domain.time_axis import Tick
from planreplan.domain.validation import ProjectIndex


class GreedyError(RuntimeError):
    """No serial placement exists inside the planning horizon."""


def _blocking_release(
    usage: list[tuple[Tick, Tick, int]], start: Tick, end: Tick, demand: int, capacity: int
) -> Tick | None:
    """Earliest tick a conflicting reservation frees, or ``None`` if it fits.

    A sweep over the endpoints inside the candidate span: if concurrent demand
    would exceed capacity anywhere, the answer is when the earliest overlapping
    reservation ends, which is the next placement worth trying. Returning that
    rather than stepping tick by tick is what keeps this cheap at hourly
    resolution.
    """
    overlapping = [(s, e, d) for s, e, d in usage if s < end and e > start]
    if not overlapping:
        return None
    points = sorted({start} | {s for s, _, _ in overlapping if start <= s < end})
    for moment in points:
        concurrent = sum(d for s, e, d in overlapping if s <= moment < e)
        if concurrent + demand > capacity:
            return min(e for s, e, d in overlapping if s <= moment < e)
    return None


def greedy_schedule(index: ProjectIndex, *, data_date: Tick = 0) -> Schedule:
    """A feasible schedule, or ``GreedyError`` if the horizon cannot hold one.

    Takes the horizon it is given rather than growing one, so it stays a pure
    function of the index. ``solve.fitted_index`` is the growing path.
    """
    relaxed = analyse(index, data_date=data_date)
    incoming: dict[str, list[Dependency]] = {task_id: [] for task_id in index.leaves}
    for link in index.leaf_dependencies:
        incoming[link.successor_id].append(link)

    # Least-float-first among topologically available tasks: the standard
    # priority rule, and deterministic because ties break on task id.
    order = sorted(
        topological_order(index.leaves, index.leaf_dependencies),
        key=lambda task_id: (relaxed.tasks[task_id].total_float, task_id),
    )
    ready: list[str] = []
    remaining = dict.fromkeys(order, 0)
    for link in index.leaf_dependencies:
        remaining[link.successor_id] += 1
    placed: dict[str, tuple[Tick, Tick]] = {}
    usage: dict[str, list[tuple[Tick, Tick, int]]] = {r.id: [] for r in index.project.resources}

    ready = [task_id for task_id in order if not remaining[task_id]]
    successors: dict[str, list[str]] = {task_id: [] for task_id in index.leaves}
    for link in index.leaf_dependencies:
        successors[link.predecessor_id].append(link.successor_id)

    while ready:
        task_id = min(ready, key=lambda t: (relaxed.tasks[t].total_float, t))
        ready.remove(task_id)
        calendar = index.effective_calendar(task_id)
        duration = index.task(task_id).duration
        start, finish = earliest_dates(index, task_id, incoming[task_id], placed, data_date)

        while True:
            release: Tick | None = None
            for assignment in index.assignments_for(task_id):
                resource = index.resource(assignment.resource_id)
                blocked = _blocking_release(
                    usage[resource.id], start, finish, assignment.demand, resource.capacity
                )
                if blocked is not None:
                    release = blocked if release is None else min(release, blocked)
            if release is None:
                break
            try:
                start = calendar.next_working_tick(release)
                finish = start + calendar.span(start, duration)
            except ValueError as exc:
                raise GreedyError(
                    f"task {task_id!r} cannot be placed before the planning horizon ends. "
                    "Validation sizes the horizon to the network's longest chain, which "
                    "contention runs past; call solve.fitted_index to widen it, or set "
                    "planning_horizon explicitly."
                ) from exc

        placed[task_id] = (start, finish)
        for assignment in index.assignments_for(task_id):
            usage[assignment.resource_id].append((start, finish, assignment.demand))
        for successor in successors[task_id]:
            remaining[successor] -= 1
            if not remaining[successor]:
                ready.append(successor)

    return Schedule(
        data_date=data_date,
        project_finish=max((finish for _, finish in placed.values()), default=data_date),
        entries={
            task_id: ScheduleEntry(
                task_id=task_id, start=start, finish=finish, state=EntryState.PLANNED
            )
            for task_id, (start, finish) in placed.items()
        },
        solver_info={"solver": "greedy-serial"},
    )
