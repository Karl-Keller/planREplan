"""Critical path method over the tick axis.

Pure functions over domain objects with no solver dependency (design rule 1),
which is what lets the monitor recompute floats on every progress update
without paying for a solve.

Textbook CPM assumes a task's span equals its duration. Here duration is work
content and span is start-dependent (ADR-8), so every bound has to travel
through the task's effective calendar rather than being added to a date. The
four dependency kinds compound this: FS and SS bound a successor's *start* and
apply directly, while FF and SF bound its *finish* and must be converted back
into a start through the calendar.

**Float is measured in working ticks**, not elapsed ones. Sixty-four ticks of
weekend is not float a scheduler can spend, and float is only comparable to
duration — which is the comparison severity and deadline jeopardy both make —
when the two share a unit. Early and late dates remain absolute ticks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict

from planreplan.domain.calendar import CalendarIndex
from planreplan.domain.entities import Dependency, DependencyKind
from planreplan.domain.time_axis import Tick
from planreplan.domain.validation import ProjectIndex


class CpmError(ValueError):
    """The network cannot be analysed as given."""


class CpmHorizonError(CpmError):
    """The schedule does not fit inside the project's planning horizon.

    A derived horizon is sized for work beginning at tick zero. Push the data
    date forward, or add enough lag and precedence, and the remaining calendar
    runs out — at which point the honest answer names the knob rather than
    leaking a calendar internal from three frames down.
    """


def _horizon_failure(task_id: str, exc: ValueError) -> CpmHorizonError:
    return CpmHorizonError(
        f"task {task_id!r} does not fit inside the planning horizon ({exc}). "
        "Set a larger planning_horizon on the project."
    )


class TaskFloat(BaseModel):
    """Early and late dates for one leaf task, with its floats."""

    model_config = ConfigDict(frozen=True)

    task_id: str
    early_start: Tick
    early_finish: Tick
    late_start: Tick
    late_finish: Tick
    total_float: int
    free_float: int

    @property
    def is_critical(self) -> bool:
        """Zero total float. Negative float is worse than critical, not better."""
        return self.total_float <= 0


class CpmResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_finish: Tick
    tasks: dict[str, TaskFloat]
    critical_path: tuple[str, ...]

    def spans(self) -> dict[str, tuple[Tick, Tick]]:
        """Early dates as spans, the shape ``overtime_report`` consumes."""
        return {t.task_id: (t.early_start, t.early_finish) for t in self.tasks.values()}


def topological_order(nodes: Sequence[str], links: Sequence[Dependency]) -> tuple[str, ...]:
    """Kahn's algorithm over the leaf network.

    Validation has already rejected cycles, so one here means the index and the
    dependencies disagree — worth failing loudly rather than looping.
    """
    incoming = dict.fromkeys(nodes, 0)
    successors: dict[str, list[str]] = {node: [] for node in nodes}
    for link in links:
        successors[link.predecessor_id].append(link.successor_id)
        incoming[link.successor_id] += 1

    ready = [node for node in nodes if not incoming[node]]
    ordered: list[str] = []
    while ready:
        node = ready.pop(0)
        ordered.append(node)
        for successor in successors[node]:
            incoming[successor] -= 1
            if not incoming[successor]:
                ready.append(successor)
    if len(ordered) != len(nodes):
        raise CpmError("dependency cycle reached the CPM engine; validation should preclude it")
    return tuple(ordered)


def _finish(calendar: CalendarIndex, start: Tick, duration: int) -> Tick:
    return start if duration == 0 else calendar.finish(start, duration)


def forward_pass(index: ProjectIndex, *, data_date: Tick = 0) -> dict[str, tuple[Tick, Tick]]:
    """Earliest start and finish for every leaf task.

    ``data_date`` is a hard lower bound on every start, which is how "now"
    enters the calculation once work is under way.
    """
    order = topological_order(index.leaves, index.leaf_dependencies)
    incoming: dict[str, list[Dependency]] = {task_id: [] for task_id in index.leaves}
    for link in index.leaf_dependencies:
        incoming[link.successor_id].append(link)

    early: dict[str, tuple[Tick, Tick]] = {}
    for task_id in order:
        try:
            early[task_id] = _early_dates(index, task_id, incoming[task_id], early, data_date)
        except ValueError as exc:
            raise _horizon_failure(task_id, exc) from exc
    return early


def _early_dates(
    index: ProjectIndex,
    task_id: str,
    incoming: Sequence[Dependency],
    early: Mapping[str, tuple[Tick, Tick]],
    data_date: Tick,
) -> tuple[Tick, Tick]:
    """Earliest start and finish for one task, given its predecessors."""
    calendar = index.effective_calendar(task_id)
    duration = index.task(task_id).duration
    earliest = calendar.next_working_tick(max(data_date, 0))
    for link in incoming:
        predecessor_start, predecessor_finish = early[link.predecessor_id]
        if link.kind is DependencyKind.FS:
            candidate = calendar.next_working_tick(max(0, predecessor_finish + link.lag))
        elif link.kind is DependencyKind.SS:
            candidate = calendar.next_working_tick(max(0, predecessor_start + link.lag))
        elif link.kind is DependencyKind.FF:
            candidate = calendar.earliest_start_for_finish(predecessor_finish + link.lag, duration)
        else:  # SF: the predecessor's start bounds the successor's finish
            candidate = calendar.earliest_start_for_finish(predecessor_start + link.lag, duration)
        earliest = max(earliest, candidate)
    earliest = calendar.next_working_tick(earliest)
    return earliest, _finish(calendar, earliest, duration)


def _finish_bound(
    calendar: CalendarIndex, link: Dependency, late: tuple[Tick, Tick], duration: int
) -> Tick:
    """Upper bound this link places on the predecessor's finish.

    FS and FF bound the finish directly. SS and SF bound the *start*, so the
    bound is snapped back to a working tick and carried forward through the
    calendar to the finish it implies — the same conversion the forward pass
    makes in the opposite direction.
    """
    successor_start, successor_finish = late
    if link.kind is DependencyKind.FS:
        return successor_start - link.lag
    if link.kind is DependencyKind.FF:
        return successor_finish - link.lag
    bound = (successor_start if link.kind is DependencyKind.SS else successor_finish) - link.lag
    snapped = calendar.previous_working_tick(bound)
    if snapped is None:
        return 0
    return _finish(calendar, snapped, duration)


def backward_pass(
    index: ProjectIndex,
    early: Mapping[str, tuple[Tick, Tick]],
    *,
    project_finish: Tick,
) -> dict[str, tuple[Tick, Tick]]:
    """Latest start and finish that do not delay ``project_finish``.

    A task deadline tightens its own late finish and the network propagates the
    consequence backwards, which is how a deadline earlier than the project's
    own finish surfaces as negative float rather than silence.

    The late *finish* is computed first and the late start derived from it.
    Doing it the other way round loses the breach: when a deadline cannot be
    met there is no legal late start to report, and falling back to the early
    start would claim zero float for a task that is already late.
    """
    order = topological_order(index.leaves, index.leaf_dependencies)
    outgoing: dict[str, list[Dependency]] = {task_id: [] for task_id in index.leaves}
    for link in index.leaf_dependencies:
        outgoing[link.predecessor_id].append(link)

    late: dict[str, tuple[Tick, Tick]] = {}
    for task_id in reversed(order):
        try:
            late[task_id] = _late_dates(
                index, task_id, outgoing[task_id], early, late, project_finish
            )
        except ValueError as exc:
            raise _horizon_failure(task_id, exc) from exc
    return late


def _late_dates(
    index: ProjectIndex,
    task_id: str,
    outgoing: Sequence[Dependency],
    early: Mapping[str, tuple[Tick, Tick]],
    late: Mapping[str, tuple[Tick, Tick]],
    project_finish: Tick,
) -> tuple[Tick, Tick]:
    """Latest start and finish for one task, given its successors."""
    calendar = index.effective_calendar(task_id)
    task = index.task(task_id)
    duration = task.duration

    ceiling = project_finish
    if task.deadline is not None:
        ceiling = min(ceiling, task.deadline)
    for link in outgoing:
        ceiling = min(ceiling, _finish_bound(calendar, link, late[link.successor_id], duration))
    ceiling = max(0, min(ceiling, index.horizon))

    latest_start = calendar.latest_start_for_finish(ceiling, duration)
    if latest_start is None:
        # The work cannot fit before the ceiling at all. The early start is the
        # only start there is, and the unmeetable ceiling is kept as the late
        # finish so the float comes out negative rather than zero.
        return early[task_id][0], ceiling
    return latest_start, _finish(calendar, latest_start, duration)


def _free_float(
    index: ProjectIndex,
    task_id: str,
    early: Mapping[str, tuple[Tick, Tick]],
    outgoing: Sequence[Dependency],
    total: int,
) -> int:
    """Delay this task can absorb without moving any successor's early dates."""
    if not outgoing:
        return total
    calendar = index.effective_calendar(task_id)
    duration = index.task(task_id).duration
    start, _ = early[task_id]
    latest: Tick | None = None
    for link in outgoing:
        successor_start, successor_finish = early[link.successor_id]
        if link.kind is DependencyKind.FS:
            bound = calendar.latest_start_for_finish(successor_start - link.lag, duration)
        elif link.kind is DependencyKind.FF:
            bound = calendar.latest_start_for_finish(successor_finish - link.lag, duration)
        elif link.kind is DependencyKind.SS:
            bound = calendar.previous_working_tick(successor_start - link.lag)
        else:
            bound = calendar.previous_working_tick(successor_finish - link.lag)
        if bound is None:
            return 0
        latest = bound if latest is None else min(latest, bound)
    if latest is None or latest < start:
        return min(total, 0)
    slack = calendar.working_prefix(latest) - calendar.working_prefix(start)
    return min(total, max(0, slack))


def analyse(index: ProjectIndex, *, data_date: Tick = 0) -> CpmResult:
    """Full CPM analysis: early dates, late dates, floats, and the critical path."""
    early = forward_pass(index, data_date=data_date)
    project_finish = max((finish for _, finish in early.values()), default=data_date)
    late = backward_pass(index, early, project_finish=project_finish)

    outgoing: dict[str, list[Dependency]] = {task_id: [] for task_id in index.leaves}
    for link in index.leaf_dependencies:
        outgoing[link.predecessor_id].append(link)

    tasks: dict[str, TaskFloat] = {}
    for task_id in index.leaves:
        calendar = index.effective_calendar(task_id)
        early_start, early_finish = early[task_id]
        late_start, late_finish = late[task_id]
        total = calendar.working_prefix(late_finish) - calendar.working_prefix(early_finish)
        tasks[task_id] = TaskFloat(
            task_id=task_id,
            early_start=early_start,
            early_finish=early_finish,
            late_start=late_start,
            late_finish=late_finish,
            total_float=total,
            free_float=_free_float(index, task_id, early, outgoing[task_id], total),
        )

    order = topological_order(index.leaves, index.leaf_dependencies)
    critical = tuple(task_id for task_id in order if tasks[task_id].is_critical)
    return CpmResult(project_finish=project_finish, tasks=tasks, critical_path=critical)
