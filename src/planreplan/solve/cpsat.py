"""CP-SAT model builder and scheduler — the solver isolation boundary.

The only module permitted to import ``ortools`` (design rule 1). Everything it
exposes speaks in domain objects, so the seam for an alternative backend stays
real rather than aspirational.

The modelling problem is the one ADR-8 creates: a task's *span* depends on
where it starts, because duration is work content and calendars have gaps. A
task is therefore constrained by a table of ``(start, end)`` pairs enumerated
from its effective calendar, which is exact and needs no arithmetic the solver
would have to invert. Where a task's whole feasible window falls inside one
working block the relation collapses to ``end == start + duration`` and the
table is skipped — the common case for continuous calendars, and much cheaper.
"""

from __future__ import annotations

from dataclasses import dataclass

from ortools.sat.python import cp_model

from planreplan.cpm import analyse
from planreplan.domain.calendar import CalendarIndex
from planreplan.domain.entities import DependencyKind
from planreplan.domain.schedule import EntryState, Schedule, ScheduleEntry
from planreplan.domain.time_axis import Tick
from planreplan.domain.validation import ProjectIndex
from planreplan.solve.horizon import fitted_index
from planreplan.solve.options import SolveOptions


class SolveError(RuntimeError):
    """No schedule could be produced."""


class InfeasibleProject(SolveError):
    """CP-SAT *proved* no schedule exists.

    Distinct from running out of time. A greedy serial placement is always
    handed to the model as a hint, so a proven infeasibility means the model
    contradicts a schedule that was constructed to satisfy the same rules —
    which is a modelling bug, not a user error, and says so.
    """


@dataclass(frozen=True, slots=True)
class SolveResult:
    """A schedule and what the solver had to say about it."""

    schedule: Schedule
    status: str
    objective: int
    best_bound: int
    wall_time: float

    @property
    def is_optimal(self) -> bool:
        return self.status == "OPTIMAL"

    @property
    def is_proven(self) -> bool:
        """Whether CP-SAT produced this, as opposed to the greedy fallback."""
        return self.status in ("OPTIMAL", "FEASIBLE")

    @property
    def gap(self) -> int:
        """Distance from the proven bound. Zero when optimality was proved."""
        return max(0, self.objective - self.best_bound)


def _span_table(
    calendar: CalendarIndex, duration: int, lower: Tick, upper: Tick
) -> list[tuple[int, int]]:
    """Every legal ``(start, end)`` for a task in ``[lower, upper]``.

    Enumerated from the calendar's blocks rather than tick by tick, so a window
    spanning a year costs one pass over a few hundred blocks.
    """
    pairs: list[tuple[int, int]] = []
    for block in calendar.blocks:
        if block.end <= lower:
            continue
        if block.start > upper:
            break
        for start in range(max(block.start, lower), min(block.end, upper + 1)):
            try:
                pairs.append((start, calendar.finish(start, duration)))
            except ValueError:  # the horizon cannot hold the work from here
                break
    return pairs


def _is_gapless(calendar: CalendarIndex, lower: Tick, upper: Tick) -> bool:
    """Whether one block covers the window, making span equal duration."""
    block = calendar._block_at_or_before(lower)
    return block is not None and block.start <= lower and upper < block.end


@dataclass(frozen=True, slots=True)
class _TaskVars:
    start: cp_model.IntVar
    end: cp_model.IntVar
    interval: cp_model.IntervalVar


class CpSatScheduler:
    """Builds and solves the RCPSP model, returning a domain ``Schedule``."""

    def __init__(self, options: SolveOptions | None = None) -> None:
        self.options = options or SolveOptions()

    def solve(self, index: ProjectIndex, *, data_date: Tick = 0) -> SolveResult:
        """Minimise makespan subject to precedence, calendars, and capacities.

        A greedy serial schedule is built first and used three ways: as the
        upper bound for every variable domain and span table, as the solver's
        starting hint, and as the answer when the time limit expires before
        CP-SAT has proved anything. It also sizes the horizon: validation
        leaves it at the network's longest chain, which is what CPM needs and
        less than a contended schedule occupies.
        """
        index, fallback = fitted_index(index, data_date=data_date)
        upper = min(fallback.project_finish, index.horizon)
        relaxed = analyse(index, data_date=data_date)

        model = cp_model.CpModel()
        variables: dict[str, _TaskVars] = {}
        for task_id in index.leaves:
            calendar = index.effective_calendar(task_id)
            duration = index.task(task_id).duration
            lower = relaxed.tasks[task_id].early_start
            start = model.new_int_var(lower, upper, f"start[{task_id}]")
            end = model.new_int_var(lower, upper, f"end[{task_id}]")

            if duration == 0:
                model.add(end == start)
            elif _is_gapless(calendar, lower, upper):
                model.add(end == start + duration)
            else:
                table = _span_table(calendar, duration, lower, upper)
                if not table:
                    raise SolveError(
                        f"task {task_id!r} cannot be placed anywhere in the planning "
                        "horizon; widen planning_horizon or shorten the task"
                    )
                model.add_allowed_assignments([start, end], table)

            # CP-SAT wants an affine size, and `end - start` over two variables
            # is not: the span is a decision here, not an offset of one variable.
            size = model.new_int_var(0, upper, f"size[{task_id}]")
            model.add(size == end - start)
            variables[task_id] = _TaskVars(
                start=start,
                end=end,
                interval=model.new_interval_var(start, size, end, f"task[{task_id}]"),
            )

            hint = fallback.entries[task_id]
            model.add_hint(start, hint.start)
            model.add_hint(end, hint.finish)

        self._add_precedence(model, index, variables)
        self._add_resources(model, index, variables)

        makespan = model.new_int_var(0, upper, "makespan")
        model.add_max_equality(makespan, [v.end for v in variables.values()])
        model.minimize(makespan)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.options.time_limit_seconds
        solver.parameters.random_seed = self.options.seed
        solver.parameters.num_workers = self.options.workers
        solver.parameters.log_search_progress = self.options.log_search
        status = solver.solve(model)
        name = solver.status_name(status)

        if status == cp_model.INFEASIBLE:
            raise InfeasibleProject(
                "CP-SAT proved this model infeasible, but a greedy serial schedule "
                "satisfying the same precedence, calendar, and capacity rules was "
                "built for it. That is a modelling defect, not an unschedulable project."
            )
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            # UNKNOWN means the clock ran out, not that nothing exists. Saying
            # otherwise would be the confident-wrong answer this system exists
            # to avoid; the greedy schedule is a real, feasible plan.
            return SolveResult(
                schedule=fallback.model_copy(
                    update={
                        "solver_info": {
                            "solver": "greedy-serial",
                            "status": name,
                            "note": "time limit reached before CP-SAT found a solution",
                        }
                    }
                ),
                status=name,
                objective=fallback.project_finish,
                best_bound=0,
                wall_time=solver.wall_time,
            )

        entries = {
            task_id: ScheduleEntry(
                task_id=task_id,
                start=int(solver.value(task.start)),
                finish=int(solver.value(task.end)),
                state=EntryState.PLANNED,
            )
            for task_id, task in variables.items()
        }
        return SolveResult(
            schedule=Schedule(
                data_date=data_date,
                project_finish=int(solver.value(makespan)),
                entries=entries,
                solver_info={
                    "solver": "cp-sat",
                    "status": name,
                    "seed": self.options.seed,
                    "workers": self.options.workers,
                },
            ),
            status=name,
            objective=int(solver.objective_value),
            best_bound=int(solver.best_objective_bound),
            wall_time=solver.wall_time,
        )

    @staticmethod
    def _add_precedence(
        model: cp_model.CpModel, index: ProjectIndex, variables: dict[str, _TaskVars]
    ) -> None:
        """The four kinds, each reading one endpoint and constraining another."""
        for link in index.leaf_dependencies:
            predecessor = variables[link.predecessor_id]
            successor = variables[link.successor_id]
            if link.kind is DependencyKind.FS:
                model.add(successor.start >= predecessor.end + link.lag)
            elif link.kind is DependencyKind.SS:
                model.add(successor.start >= predecessor.start + link.lag)
            elif link.kind is DependencyKind.FF:
                model.add(successor.end >= predecessor.end + link.lag)
            else:  # SF
                model.add(successor.end >= predecessor.start + link.lag)

    @staticmethod
    def _add_resources(
        model: cp_model.CpModel, index: ProjectIndex, variables: dict[str, _TaskVars]
    ) -> None:
        """One cumulative per resource, at constant capacity.

        Capacity is constant because unavailability is already folded into the
        effective calendars, so a task cannot occupy a tick its resources are
        away for. A task's interval does span its internal idle time and so
        holds the resource across it, which is deliberate: crews are not
        released to another foreman mid-pour.
        """
        for resource in index.project.resources:
            intervals = []
            demands = []
            for task_id in index.leaves:
                for assignment in index.assignments_for(task_id):
                    if assignment.resource_id == resource.id:
                        intervals.append(variables[task_id].interval)
                        demands.append(assignment.demand)
            if intervals:
                model.add_cumulative(intervals, demands, resource.capacity)
