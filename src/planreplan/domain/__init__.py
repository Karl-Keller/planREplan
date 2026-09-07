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
from planreplan.domain.entities import (
    Assignment,
    Dependency,
    DependencyKind,
    Endpoint,
    PayClass,
    PayRules,
    Project,
    Resource,
    Task,
    TaskStatus,
)
from planreplan.domain.overtime import (
    OvertimeReport,
    OvertimeResolutionError,
    Span,
    overtime_report,
)
from planreplan.domain.schedule import (
    Baseline,
    EntryState,
    Schedule,
    ScheduleEntry,
)
from planreplan.domain.time_axis import Tick, TimeAxis
from planreplan.domain.validation import (
    ProjectIndex,
    ProjectValidationError,
    validate_project,
)

__all__ = [
    "Assignment",
    "Baseline",
    "Calendar",
    "CalendarException",
    "CalendarIndex",
    "DayOfWeek",
    "Dependency",
    "DependencyKind",
    "Endpoint",
    "EntryState",
    "OvertimeReport",
    "OvertimeResolutionError",
    "PayClass",
    "PayRules",
    "Project",
    "ProjectIndex",
    "ProjectValidationError",
    "Resource",
    "Schedule",
    "ScheduleEntry",
    "Shift",
    "Span",
    "Task",
    "TaskStatus",
    "Tick",
    "TimeAxis",
    "WorkBlock",
    "overtime_report",
    "validate_project",
]
