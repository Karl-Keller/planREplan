"""JSON persistence for a project directory (ADR-4).

A project directory is transparent, diffable, and git-friendly:

.. code-block:: text

    project.json      the entities, with a schema version
    schedules/        immutable schedule JSONs (Phase 2)
    events.jsonl      append-only progress and disruption log (Phase 3)

Only ``project.json`` exists yet; the other two arrive with the types they
hold. The directory is created with all three slots so the layout is a fact on
disk rather than a promise in a document.

Whole-project validation is available here but not automatic. Parsing already
rejects a malformed entity, while :func:`~planreplan.domain.validate_project`
builds calendars and returns an index worth keeping — so a caller that wants
the index asks for it once rather than paying for it twice.
"""

from __future__ import annotations

import json
from pathlib import Path

from planreplan.domain.entities import Project
from planreplan.domain.validation import validate_project

#: Schema version this build writes. Bump when the persisted shape changes in a
#: way an older reader would misinterpret.
SCHEMA_VERSION = 1

PROJECT_FILE = "project.json"
SCHEDULES_DIR = "schedules"
EVENTS_FILE = "events.jsonl"


class SchemaVersionError(ValueError):
    """A project file this build cannot read correctly."""


def _check_schema_version(version: int, source: Path) -> None:
    """Refuse rather than guess.

    A newer file may contain fields with meanings this build does not know, and
    reading it as though it were current would corrupt a schedule quietly. An
    older file is a migration that has not been written; there is exactly one
    version so far, and inventing a migration framework for it would be
    speculative. The hook goes in when the second version does.
    """
    if version > SCHEMA_VERSION:
        raise SchemaVersionError(
            f"{source} declares schema version {version}, but this build reads "
            f"version {SCHEMA_VERSION}. Upgrade planreplan to open it."
        )
    if version < SCHEMA_VERSION:
        raise SchemaVersionError(
            f"{source} declares schema version {version}; this build reads version "
            f"{SCHEMA_VERSION} and no migration from {version} exists."
        )


def save_project(project: Project, directory: Path) -> Path:
    """Write ``project`` into a project directory, creating it if needed.

    Returns the path of the written ``project.json``.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / SCHEDULES_DIR).mkdir(exist_ok=True)
    (directory / EVENTS_FILE).touch(exist_ok=True)

    stored = project.model_copy(update={"schema_version": SCHEMA_VERSION})
    target = directory / PROJECT_FILE
    # indent + trailing newline: the file is meant to be read and diffed.
    target.write_text(
        json.dumps(json.loads(stored.model_dump_json()), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def load_project(directory: Path, *, validate: bool = False) -> Project:
    """Read a project directory.

    Args:
        directory: the project directory, or the ``project.json`` itself.
        validate: also run the whole-project pass, discarding the index. Callers
            wanting the index should leave this off and call ``validate_project``
            themselves rather than build calendars twice.

    Raises:
        SchemaVersionError: if the file was written by a different schema.
        pydantic.ValidationError: if an entity is malformed.
        ProjectValidationError: if ``validate`` is set and the project is invalid.
    """
    path = Path(directory)
    if path.is_dir():
        path = path / PROJECT_FILE
    if not path.exists():
        raise FileNotFoundError(f"no project file at {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SchemaVersionError(f"{path} does not contain a project object")
    _check_schema_version(int(raw.get("schema_version", 0)), path)

    project = Project.model_validate(raw)
    if validate:
        validate_project(project)
    return project
