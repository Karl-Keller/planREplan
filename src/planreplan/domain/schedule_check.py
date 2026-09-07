"""Verification of a concrete schedule against a project.

``02-architecture.md`` originally placed ``FeasibilityChecker`` inside
``solve/`` as a second face of the CP-SAT adapter. That conflated two different
questions. Asking *is this project satisfiable at all* genuinely needs a
solver — it is a search. Asking *does this particular schedule obey the rules*
does not: it is a linear sweep over the same constraints, and doing it without
OR-Tools makes design rule 1 stronger rather than weaker. The monitor can then
verify a plan on every progress update, and the proposal gate of ADR-6 can
reject an LLM's suggestion without paying for a solve.

Every violation is reported, not just the first. A schedule with three problems
should say so once.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from planreplan.domain.entities import DependencyKind
from planreplan.domain.schedule import EntryState, Schedule
from planreplan.domain.validation import ProjectIndex


class ViolationKind(StrEnum):
    MISSING_TASK = "missing_task"
    UNKNOWN_TASK = "unknown_task"
    NON_WORKING_START = "non_working_start"
    WRONG_WORK_CONTENT = "wrong_work_content"
    PRECEDENCE = "precedence"
    RESOURCE_OVERLOAD = "resource_overload"
    DATA_DATE = "data_date"


class Violation(BaseModel):
    """One way a schedule fails to be executable as written."""

    model_config = ConfigDict(frozen=True)

    kind: ViolationKind
    task_ids: tuple[str, ...]
    detail: str


def _coverage(index: ProjectIndex, schedule: Schedule) -> list[Violation]:
    """Full leaf coverage is what makes churn and variance computable."""
    found: list[Violation] = []
    scheduled = set(schedule.entries)
    leaves = set(index.leaves)
    for task_id in sorted(leaves - scheduled):
        found.append(
            Violation(
                kind=ViolationKind.MISSING_TASK,
                task_ids=(task_id,),
                detail=f"leaf task {task_id!r} has no entry",
            )
        )
    for task_id in sorted(scheduled - leaves):
        found.append(
            Violation(
                kind=ViolationKind.UNKNOWN_TASK,
                task_ids=(task_id,),
                detail=f"entry {task_id!r} is not a leaf task of this project",
            )
        )
    return found


def _calendars(index: ProjectIndex, schedule: Schedule) -> list[Violation]:
    """Work must land on working ticks, and there must be exactly enough of it."""
    found: list[Violation] = []
    for task_id in index.leaves:
        entry = schedule.entries.get(task_id)
        if entry is None:
            continue
        calendar = index.effective_calendar(task_id)
        duration = index.task(task_id).duration
        if duration and not calendar.is_working(entry.start):
            found.append(
                Violation(
                    kind=ViolationKind.NON_WORKING_START,
                    task_ids=(task_id,),
                    detail=f"{task_id!r} starts at tick {entry.start}, when nobody is working",
                )
            )
            continue
        worked = calendar.working_prefix(entry.finish) - calendar.working_prefix(entry.start)
        if worked != duration:
            found.append(
                Violation(
                    kind=ViolationKind.WRONG_WORK_CONTENT,
                    task_ids=(task_id,),
                    detail=(
                        f"{task_id!r} spans {worked} working ticks between {entry.start} "
                        f"and {entry.finish}, but its duration is {duration}"
                    ),
                )
            )
    return found


def _precedence(index: ProjectIndex, schedule: Schedule) -> list[Violation]:
    found: list[Violation] = []
    for link in index.leaf_dependencies:
        predecessor = schedule.entries.get(link.predecessor_id)
        successor = schedule.entries.get(link.successor_id)
        if predecessor is None or successor is None:
            continue
        if link.kind is DependencyKind.FS:
            actual, required = successor.start, predecessor.finish + link.lag
        elif link.kind is DependencyKind.SS:
            actual, required = successor.start, predecessor.start + link.lag
        elif link.kind is DependencyKind.FF:
            actual, required = successor.finish, predecessor.finish + link.lag
        else:
            actual, required = successor.finish, predecessor.start + link.lag
        if actual < required:
            found.append(
                Violation(
                    kind=ViolationKind.PRECEDENCE,
                    task_ids=(link.predecessor_id, link.successor_id),
                    detail=(
                        f"{link.kind} {link.predecessor_id!r} -> {link.successor_id!r} "
                        f"(lag {link.lag}) needs {required}, but the schedule has {actual}"
                    ),
                )
            )
    return found


def _resources(index: ProjectIndex, schedule: Schedule) -> list[Violation]:
    """Capacity checked by a sweep over reservation starts.

    Concurrent demand can only rise at a start, so those are the only moments
    worth testing — which is why this stays linear rather than walking ticks.
    """
    found: list[Violation] = []
    for resource in index.project.resources:
        reservations = [
            (
                schedule.entries[a.task_id].start,
                schedule.entries[a.task_id].finish,
                a.demand,
                a.task_id,
            )
            for task_id in index.leaves
            for a in index.assignments_for(task_id)
            if a.resource_id == resource.id and a.task_id in schedule.entries
        ]
        for moment, _, _, _ in sorted(reservations):
            active = [(d, t) for s, e, d, t in reservations if s <= moment < e]
            total = sum(d for d, _ in active)
            if total > resource.capacity:
                found.append(
                    Violation(
                        kind=ViolationKind.RESOURCE_OVERLOAD,
                        task_ids=tuple(sorted(t for _, t in active)),
                        detail=(
                            f"resource {resource.id!r} is asked for {total} at tick {moment}, "
                            f"but has capacity {resource.capacity}"
                        ),
                    )
                )
                break  # one report per resource; the rest is the same story
    return found


def _data_date(schedule: Schedule) -> list[Violation]:
    """Nothing unstarted may begin in the past, and nothing done may end in the future."""
    found: list[Violation] = []
    for task_id, entry in sorted(schedule.entries.items()):
        if entry.state is EntryState.PLANNED and entry.start < schedule.data_date:
            found.append(
                Violation(
                    kind=ViolationKind.DATA_DATE,
                    task_ids=(task_id,),
                    detail=(
                        f"{task_id!r} is unstarted but begins at {entry.start}, "
                        f"before the data date {schedule.data_date}"
                    ),
                )
            )
        elif entry.state is EntryState.COMPLETE and entry.finish > schedule.data_date:
            found.append(
                Violation(
                    kind=ViolationKind.DATA_DATE,
                    task_ids=(task_id,),
                    detail=(
                        f"{task_id!r} is complete but finishes at {entry.finish}, "
                        f"after the data date {schedule.data_date}"
                    ),
                )
            )
        elif entry.state is EntryState.IN_PROGRESS and entry.effective_resume < schedule.data_date:
            found.append(
                Violation(
                    kind=ViolationKind.DATA_DATE,
                    task_ids=(task_id,),
                    detail=(
                        f"{task_id!r} is in progress but resumes at {entry.effective_resume}, "
                        f"before the data date {schedule.data_date}"
                    ),
                )
            )
    return found


def check_schedule(index: ProjectIndex, schedule: Schedule) -> tuple[Violation, ...]:
    """Every way ``schedule`` fails to be executable against ``index``.

    An empty result means the schedule obeys coverage, calendars, precedence,
    capacity, and the data date. It says nothing about optimality.
    """
    found = _coverage(index, schedule)
    found += _calendars(index, schedule)
    found += _precedence(index, schedule)
    found += _resources(index, schedule)
    found += _data_date(schedule)
    return tuple(found)
