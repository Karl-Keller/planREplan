"""Solver configuration — the one place a time-limit/quality knob lives (NFR2)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SolveOptions(BaseModel):
    """How hard to try, and how reproducibly.

    Determinism is not optional in this system (NFR1, design rule 2), and
    CP-SAT is only deterministic with a fixed seed *and* a fixed worker count:
    parallel workers race, so eight of them can return different optimal-cost
    solutions on identical input. ``workers=1`` is therefore the default rather
    than a testing special case, and raising it is an explicit trade of
    reproducibility for speed.
    """

    model_config = ConfigDict(frozen=True)

    time_limit_seconds: float = Field(default=10.0, gt=0)
    seed: int = 0
    workers: int = Field(default=1, ge=1)
    log_search: bool = False
