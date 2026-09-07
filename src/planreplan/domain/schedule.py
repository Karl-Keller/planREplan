"""Immutable schedules and baselines.

``docs/03-domain-model.md``: a ``Schedule`` covers **every leaf task**, not
only the unstarted ones. Dropping tasks as they start would shrink the
comparison domain, so churn would silently measure a different set on each
repair, a kickoff baseline could not be diffed against today's plan, and
planned-versus-actual variance would be unanswerable.

Pinning is per *endpoint*, not per task. A ``PLANNED`` entry is freely movable;
an ``IN_PROGRESS`` entry has a pinned start with its remainder still a
decision; a ``COMPLETE`` entry is pinned at both ends.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, computed_field, model_validator

from planreplan.domain.time_axis import Tick


class EntryState(StrEnum):
    """State of one task *as of the schedule's data date*.

    Distinct from ``Task.status``, which is current project state. A frozen
    baseline records what was true when it was frozen, which is exactly why it
    still means something years later.
    """

    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"


class ScheduleEntry(BaseModel):
    """One leaf task's place in a schedule.

    ``finish`` is stored rather than recomputed. Span depends on the effective
    calendar, which depends on assignments, so a derived finish would let a
    calendar edit next month silently change what a baseline frozen last year
    *meant* — an immutable object with mutable implications.
    """

    model_config = ConfigDict(frozen=True)

    task_id: str
    start: Tick
    finish: Tick
    state: EntryState = EntryState.PLANNED
    resume_at: Tick | None = None

    @model_validator(mode="after")
    def _endpoints_are_ordered(self) -> ScheduleEntry:
        if self.finish < self.start:
            raise ValueError(f"task {self.task_id!r} finishes at {self.finish}, before its start")
        if self.resume_at is not None and not self.start <= self.resume_at <= self.finish:
            raise ValueError(
                f"task {self.task_id!r} resumes at {self.resume_at}, outside "
                f"[{self.start}, {self.finish}]"
            )
        return self

    @property
    def effective_resume(self) -> Tick:
        """Where remaining work continues; the start for anything not begun."""
        return self.start if self.resume_at is None else self.resume_at

    @property
    def is_pinned_start(self) -> bool:
        """A start that is an observed fact rather than a decision."""
        return self.state is not EntryState.PLANNED


class Schedule(BaseModel):
    """An immutable, content-addressed schedule.

    The id is a hash of the content, not a random value: identical schedules
    have identical ids, a `ChangeSet` referencing one names something
    reproducible, and determinism (NFR1) survives. A UUID would have broken all
    three for no gain.
    """

    model_config = ConfigDict(frozen=True)

    data_date: Tick = 0
    project_finish: Tick = 0
    entries: dict[str, ScheduleEntry]
    solver_info: dict[str, str | int | float | bool] = {}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> str:
        """Stable digest of the schedule's content."""
        payload = json.dumps(
            {
                "data_date": self.data_date,
                "project_finish": self.project_finish,
                "entries": {
                    task_id: [
                        entry.start,
                        entry.finish,
                        entry.state.value,
                        entry.resume_at,
                    ]
                    for task_id, entry in sorted(self.entries.items())
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def entry(self, task_id: str) -> ScheduleEntry:
        return self.entries[task_id]

    def spans(self) -> dict[str, tuple[Tick, Tick]]:
        """Task spans, the shape overtime aggregation consumes."""
        return {task_id: (e.start, e.finish) for task_id, e in self.entries.items()}


class Baseline(BaseModel):
    """A schedule with a name and a freeze timestamp. Frozen forever."""

    model_config = ConfigDict(frozen=True)

    name: str
    frozen_at: datetime
    schedule: Schedule
