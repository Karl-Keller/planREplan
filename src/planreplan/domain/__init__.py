"""Domain entities and the ubiquitous language of the system.

Normative reference: ``docs/03-domain-model.md``. Pydantic v2 models, frozen
where the design calls for immutability, plus a ``validate_project`` pass that
makes invalid states unrepresentable inside the core.

Carries no solver dependency (design rule 1) and no LLM dependency (rule 6).
Overtime aggregation lives here because it is a pure function over domain
objects. Phase 1.
"""

from planreplan.domain.calendar import (
    Calendar,
    CalendarException,
    CalendarIndex,
    DayOfWeek,
    Shift,
    WorkBlock,
)
from planreplan.domain.time_axis import Tick, TimeAxis

__all__ = [
    "Calendar",
    "CalendarException",
    "CalendarIndex",
    "DayOfWeek",
    "Shift",
    "Tick",
    "TimeAxis",
    "WorkBlock",
]
