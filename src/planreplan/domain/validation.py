"""Whole-project validation and the derived view that follows from it.

``docs/03-domain-model.md``: invalid states are unrepresentable inside the
core, and any violation is an error at the io or proposal boundary. Entity
models enforce what one entity can check alone; everything requiring a view of
the whole project lives here.

Problems are collected rather than raised one at a time. A scheduler importing
a real project wants the list, not a game of whack-a-mole, and the eventual LLM
proposal gate needs every reason a proposal was rejected in order to explain
itself.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence

from planreplan.domain.calendar import Calendar, CalendarIndex
from planreplan.domain.entities import (
    Assignment,
    Dependency,
    Endpoint,
    Project,
    Resource,
    Task,
)
from planreplan.domain.time_axis import Tick

#: Ceiling on horizon doubling. Reached only by a calendar so sparse that the
#: work cannot fit in any sane span, which is a modelling error worth naming.
_MAX_HORIZON_DOUBLINGS = 32


class ProjectValidationError(ValueError):
    """Every problem found in one pass."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        joined = "\n  - ".join(self.problems)
        super().__init__(f"{len(self.problems)} problem(s) in project:\n  - {joined}")


class ProjectIndex:
    """Validated, derived view of a :class:`Project`.

    Held separately from ``Project`` rather than cached on it: the entities are
    frozen values, and hanging a mutable calendar cache off them would make an
    immutable object quietly stateful. Building the index *is* validation, so
    possessing one is evidence the project passed.
    """

    __slots__ = (
        "_assignments_by_task",
        "_calendars",
        "_children",
        "_effective",
        "_resource_calendars",
        "_resources",
        "_tasks",
        "_wbs_paths",
        "horizon",
        "leaf_dependencies",
        "leaves",
        "project",
    )

    def __init__(
        self,
        project: Project,
        horizon: Tick,
        leaf_dependencies: tuple[Dependency, ...],
    ) -> None:
        self.project = project
        self.horizon = horizon
        self.leaf_dependencies = leaf_dependencies
        self._tasks: Mapping[str, Task] = {t.id: t for t in project.tasks}
        self._resources: Mapping[str, Resource] = {r.id: r for r in project.resources}
        self._calendars: Mapping[str, Calendar] = {c.id: c for c in project.calendars}

        children: dict[str | None, list[str]] = {}
        for task in project.tasks:
            children.setdefault(task.parent_id, []).append(task.id)
        self._children: Mapping[str | None, tuple[str, ...]] = {
            parent: tuple(kids) for parent, kids in children.items()
        }
        self.leaves: tuple[str, ...] = tuple(
            t.id for t in project.tasks if t.id not in self._children
        )
        self._wbs_paths = dict(self._compute_wbs_paths())

        by_task: dict[str, list[Assignment]] = {}
        for assignment in project.assignments:
            by_task.setdefault(assignment.task_id, []).append(assignment)
        self._assignments_by_task: Mapping[str, tuple[Assignment, ...]] = {
            task_id: tuple(items) for task_id, items in by_task.items()
        }

        self._resource_calendars: dict[str, CalendarIndex] = {}
        self._effective: dict[tuple[str, tuple[str, ...]], CalendarIndex] = {}

    # -- structure ---------------------------------------------------------

    def task(self, task_id: str) -> Task:
        return self._tasks[task_id]

    def resource(self, resource_id: str) -> Resource:
        return self._resources[resource_id]

    def children_of(self, task_id: str | None) -> tuple[str, ...]:
        return self._children.get(task_id, ())

    def is_leaf(self, task_id: str) -> bool:
        return task_id not in self._children

    def leaves_under(self, task_id: str) -> tuple[str, ...]:
        """Leaf descendants, or the task itself when it is already a leaf."""
        if self.is_leaf(task_id):
            return (task_id,)
        found: list[str] = []
        for child in self.children_of(task_id):
            found.extend(self.leaves_under(child))
        return tuple(found)

    def wbs_path(self, task_id: str) -> str:
        """Display path such as ``1.3.2``, derived from the tree every time."""
        return self._wbs_paths[task_id]

    def _compute_wbs_paths(self) -> Iterator[tuple[str, str]]:
        stack: list[tuple[str, str]] = [
            (task_id, str(position))
            for position, task_id in enumerate(self.children_of(None), start=1)
        ]
        # Pushed in reverse so a depth-first pop still visits siblings in
        # declaration order, and paths read the way the WBS is written.
        stack.reverse()
        while stack:
            task_id, path = stack.pop()
            yield task_id, path
            children = [
                (child, f"{path}.{position}")
                for position, child in enumerate(self.children_of(task_id), start=1)
            ]
            stack.extend(reversed(children))

    def assignments_for(self, task_id: str) -> tuple[Assignment, ...]:
        return self._assignments_by_task.get(task_id, ())

    # -- calendars ---------------------------------------------------------

    def calendar_for_resource(self, resource_id: str) -> CalendarIndex:
        """Base calendar with the resource's own exceptions layered on top."""
        cached = self._resource_calendars.get(resource_id)
        if cached is not None:
            return cached
        resource = self._resources[resource_id]
        base = self._calendars[resource.calendar_id or self.project.default_calendar_id]
        if resource.exceptions:
            # Appended, so a resource-specific override beats the shared one:
            # the last matching exception wins.
            base = base.model_copy(update={"exceptions": base.exceptions + resource.exceptions})
        built = CalendarIndex.build(base, self.project.axis, self.horizon)
        self._resource_calendars[resource_id] = built
        return built

    def effective_calendar(self, task_id: str) -> CalendarIndex:
        """The task's calendar intersected with every assigned resource's.

        Work happens only when the task and all its resources agree, so this is
        an intersection. Cached by the calendar it is built from rather than by
        task, since tasks sharing a trade and a crew share an answer.
        """
        task = self._tasks[task_id]
        calendar_id = task.calendar_id or self.project.default_calendar_id
        resource_ids = tuple(sorted(a.resource_id for a in self.assignments_for(task_id)))
        key = (calendar_id, resource_ids)
        cached = self._effective.get(key)
        if cached is not None:
            return cached
        index = CalendarIndex.build(self._calendars[calendar_id], self.project.axis, self.horizon)
        for resource_id in resource_ids:
            index = index.intersect(self.calendar_for_resource(resource_id))
        self._effective[key] = index
        return index


# -- expansion of summary-level dependencies -------------------------------


def _expansion_problem(dependency: Dependency, index: ProjectIndex) -> str | None:
    """Why this summary-level link cannot be expanded to leaves, if it cannot.

    A summary's start is the earliest of its leaves and its finish the latest.
    Replacing a link with one link per leaf is a conjunction, which reproduces a
    *maximum* exactly and a *minimum* only by over-constraining. So a link is
    expandable when it reads the predecessor's finish (FS, FF) and when it
    constrains the successor's start (FS, SS); the other combinations would
    silently tighten the schedule, and tightening a plan without saying so is
    the failure this whole system exists to avoid.
    """
    predecessor_is_summary = not index.is_leaf(dependency.predecessor_id)
    successor_is_summary = not index.is_leaf(dependency.successor_id)
    if predecessor_is_summary and dependency.kind.predecessor_endpoint is Endpoint.START:
        return (
            f"dependency {dependency.predecessor_id!r} -> {dependency.successor_id!r} "
            f"({dependency.kind}) reads the start of summary task "
            f"{dependency.predecessor_id!r}; a summary's start is the earliest of its "
            "leaves and cannot be expanded without over-constraining. Link a leaf task."
        )
    if successor_is_summary and dependency.kind.successor_endpoint is Endpoint.FINISH:
        return (
            f"dependency {dependency.predecessor_id!r} -> {dependency.successor_id!r} "
            f"({dependency.kind}) constrains the finish of summary task "
            f"{dependency.successor_id!r}; a summary's finish is the latest of its "
            "leaves and cannot be expanded without over-constraining. Link a leaf task."
        )
    return None


def _expand_to_leaves(dependency: Dependency, index: ProjectIndex) -> list[Dependency]:
    return [
        Dependency(
            predecessor_id=predecessor,
            successor_id=successor,
            kind=dependency.kind,
            lag=dependency.lag,
        )
        for predecessor in index.leaves_under(dependency.predecessor_id)
        for successor in index.leaves_under(dependency.successor_id)
        if predecessor != successor
    ]


def _find_cycle(edges: Sequence[tuple[str, str]]) -> list[str] | None:
    """A cycle in the leaf dependency graph, named so the error is actionable."""
    adjacency: dict[str, list[str]] = {}
    for source, target in edges:
        adjacency.setdefault(source, []).append(target)
    UNSEEN, ACTIVE, DONE = 0, 1, 2
    state: dict[str, int] = {}
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = ACTIVE
        path.append(node)
        for neighbour in adjacency.get(node, ()):
            status = state.get(neighbour, UNSEEN)
            if status == ACTIVE:
                return [*path[path.index(neighbour) :], neighbour]
            if status == UNSEEN:
                found = visit(neighbour)
                if found is not None:
                    return found
        path.pop()
        state[node] = DONE
        return None

    for node in list(adjacency):
        if state.get(node, UNSEEN) == UNSEEN:
            cycle = visit(node)
            if cycle is not None:
                return cycle
    return None


# -- the validation pass ---------------------------------------------------


def _structural_problems(project: Project) -> list[str]:
    """Checks that need no calendars, and must pass before any are built."""
    problems: list[str] = []

    for label, ids in (
        ("task", [t.id for t in project.tasks]),
        ("resource", [r.id for r in project.resources]),
        ("calendar", [c.id for c in project.calendars]),
        ("pay rules", [p.id for p in project.pay_rules]),
    ):
        seen: set[str] = set()
        for identifier in ids:
            if identifier in seen:
                problems.append(f"duplicate {label} id {identifier!r}")
            seen.add(identifier)

    task_ids = {t.id for t in project.tasks}
    resource_ids = {r.id for r in project.resources}
    calendar_ids = {c.id for c in project.calendars}
    pay_rules_ids = {p.id for p in project.pay_rules}

    if project.default_calendar_id not in calendar_ids:
        problems.append(
            f"default_calendar_id {project.default_calendar_id!r} is not among the "
            "project's calendars"
        )
    if project.default_pay_rules_id and project.default_pay_rules_id not in pay_rules_ids:
        problems.append(
            f"default_pay_rules_id {project.default_pay_rules_id!r} is not among the "
            "project's pay rules"
        )

    children: dict[str, list[str]] = {}
    for task in project.tasks:
        if task.calendar_id and task.calendar_id not in calendar_ids:
            problems.append(f"task {task.id!r} references unknown calendar {task.calendar_id!r}")
        if task.parent_id is not None:
            if task.parent_id not in task_ids:
                problems.append(f"task {task.id!r} references unknown parent {task.parent_id!r}")
            else:
                children.setdefault(task.parent_id, []).append(task.id)
        if task.deadline is not None and task.deadline < 0:
            problems.append(f"task {task.id!r} has a deadline before the project epoch")

    problems.extend(_wbs_cycle_problems(project))

    for task in project.tasks:
        is_leaf = task.id not in children
        if not is_leaf and task.duration:
            problems.append(
                f"summary task {task.id!r} carries duration {task.duration}; summaries derive "
                "their span from their children"
            )
        if not is_leaf and task.is_milestone:
            problems.append(f"task {task.id!r} is a milestone with children")
        if is_leaf and not task.is_milestone and task.duration <= 0:
            problems.append(f"leaf task {task.id!r} has no duration")

    for resource in project.resources:
        if resource.calendar_id and resource.calendar_id not in calendar_ids:
            problems.append(
                f"resource {resource.id!r} references unknown calendar {resource.calendar_id!r}"
            )
        if resource.pay_rules_id and resource.pay_rules_id not in pay_rules_ids:
            problems.append(
                f"resource {resource.id!r} references unknown pay rules {resource.pay_rules_id!r}"
            )

    by_id = {r.id: r for r in project.resources}
    for assignment in project.assignments:
        if assignment.task_id not in task_ids:
            problems.append(f"assignment references unknown task {assignment.task_id!r}")
        elif assignment.task_id in children:
            problems.append(
                f"assignment targets summary task {assignment.task_id!r}; only leaves "
                "carry assignments"
            )
        if assignment.resource_id not in resource_ids:
            problems.append(f"assignment references unknown resource {assignment.resource_id!r}")
        elif assignment.demand > by_id[assignment.resource_id].capacity:
            problems.append(
                f"assignment of {assignment.task_id!r} demands {assignment.demand} of "
                f"resource {assignment.resource_id!r}, which has capacity "
                f"{by_id[assignment.resource_id].capacity}"
            )

    for dependency in project.dependencies:
        for role, endpoint in (
            ("predecessor", dependency.predecessor_id),
            ("successor", dependency.successor_id),
        ):
            if endpoint not in task_ids:
                problems.append(f"dependency {role} {endpoint!r} is not a task in this project")

    return problems


def _wbs_cycle_problems(project: Project) -> list[str]:
    """A parent chain that never reaches a root is a cycle, not a deep tree."""
    parents = {t.id: t.parent_id for t in project.tasks}
    problems: list[str] = []
    for task_id in parents:
        seen = {task_id}
        current = parents[task_id]
        while current is not None and current in parents:
            if current in seen:
                problems.append(f"task {task_id!r} is its own ancestor in the WBS")
                break
            seen.add(current)
            current = parents[current]
    return problems


def _total_work_required(project: Project, leaves: Sequence[str]) -> int:
    by_id = {t.id: t for t in project.tasks}
    work = sum(by_id[task_id].duration for task_id in leaves)
    lag = sum(max(0, d.lag) for d in project.dependencies)
    return work + lag


def _min_working_time(project: Project, dependencies: tuple[Dependency, ...], horizon: Tick) -> int:
    candidate = ProjectIndex(project, horizon, dependencies)
    if not candidate.leaves:
        return 0
    return min(candidate.effective_calendar(task_id).total_work for task_id in candidate.leaves)


def _derive_horizon(project: Project, dependencies: tuple[Dependency, ...], required: int) -> Tick:
    """Smallest doubling that fits the work, checked against real calendars.

    Deriving rather than demanding one keeps ordinary use free of a number
    nobody wants to guess, while an explicit ``planning_horizon`` stays
    available for a project that knows its own end.

    The test is that *every* effective calendar can supply the whole project's
    work, not merely each task's own. Tasks may be strictly sequential, so a
    horizon that fits the largest task can still fall short of the chain; being
    generous costs a few hundred blocks and being wrong costs a spurious
    failure.

    Doubling stops as soon as it stops buying working time. A task whose
    calendars never agree has an empty effective calendar, and no horizon
    however large will change that — growing until an internal limit would turn
    a precise, reportable modelling error into an overflow.
    """
    if not required:
        return max(project.axis.ticks_per_day * 7, 1)
    horizon = max(required, project.axis.ticks_per_day * 7)
    previous = -1
    for _ in range(_MAX_HORIZON_DOUBLINGS):
        available = _min_working_time(project, dependencies, horizon)
        if available >= required:
            return horizon
        if available <= previous:
            # More axis is not producing more working time; validate_project
            # reports the offending task by name.
            return horizon
        previous = available
        horizon *= 2
    return horizon


def validate_project(project: Project) -> ProjectIndex:
    """Validate ``project`` and return the derived view.

    Raises:
        ProjectValidationError: with every problem found, not just the first.
    """
    problems = _structural_problems(project)
    if problems:
        raise ProjectValidationError(problems)

    provisional = ProjectIndex(project, project.planning_horizon or 1, ())

    expanded: list[Dependency] = []
    for dependency in project.dependencies:
        problem = _expansion_problem(dependency, provisional)
        if problem:
            problems.append(problem)
            continue
        expanded.extend(_expand_to_leaves(dependency, provisional))

    cycle = _find_cycle([(d.predecessor_id, d.successor_id) for d in expanded])
    if cycle:
        problems.append("dependency cycle: " + " -> ".join(cycle))

    if problems:
        raise ProjectValidationError(problems)

    links = tuple(expanded)
    leaves = ProjectIndex(project, 1, links).leaves
    horizon = project.planning_horizon or _derive_horizon(
        project, links, _total_work_required(project, leaves)
    )
    index = ProjectIndex(project, horizon, links)

    for task_id in index.leaves:
        calendar = index.effective_calendar(task_id)
        if calendar.total_work == 0:
            problems.append(
                f"task {task_id!r} has no working time: its calendar and its assigned "
                "resources' calendars never agree"
            )
        elif calendar.total_work < index.task(task_id).duration:
            problems.append(
                f"task {task_id!r} needs {index.task(task_id).duration} working ticks but its "
                f"effective calendar offers only {calendar.total_work} within the horizon"
            )

    if problems:
        raise ProjectValidationError(problems)
    return index
