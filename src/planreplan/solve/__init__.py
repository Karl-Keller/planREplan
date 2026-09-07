"""CP-SAT model builder and adapters — the solver isolation boundary.

This is the only package permitted to import ``ortools`` (design rule 1). It
exposes ``CpSatScheduler`` in domain terms, translating at the boundary, which
keeps the seam open for an alternative backend.

The working-time relation of ADR-8 is materialised here, never in ``domain``:
the domain owns ``Calendar`` as a predicate, ``solve`` turns it into the tables
CP-SAT needs. Phase 2.
"""

from planreplan.solve.cpsat import (
    CpSatScheduler,
    InfeasibleProject,
    SolveError,
    SolveResult,
)
from planreplan.solve.greedy import GreedyError, greedy_schedule
from planreplan.solve.options import SolveOptions

__all__ = [
    "CpSatScheduler",
    "GreedyError",
    "InfeasibleProject",
    "SolveError",
    "SolveOptions",
    "SolveResult",
    "greedy_schedule",
]
