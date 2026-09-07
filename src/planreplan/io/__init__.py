"""Persistence and interchange: JSON project directories, later XER and MSP XML.

Converts between wall-clock dates and integer ticks at the boundary so that no
core component performs datetime arithmetic (design rule 5), and reconciles
``percentComplete`` into the authoritative ``remainingDuration``.

The package name shadows the standard library's ``io`` only inside this
package's own namespace; absolute imports elsewhere resolve to the stdlib as
usual. Phase 1 for JSON, Phase 6+ for the proprietary formats.
"""

from planreplan.io.json_store import (
    EVENTS_FILE,
    PROJECT_FILE,
    SCHEDULES_DIR,
    SCHEMA_VERSION,
    SchemaVersionError,
    load_project,
    save_project,
)

__all__ = [
    "EVENTS_FILE",
    "PROJECT_FILE",
    "SCHEDULES_DIR",
    "SCHEMA_VERSION",
    "SchemaVersionError",
    "load_project",
    "save_project",
]
