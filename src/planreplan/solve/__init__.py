"""CP-SAT model builder and adapters — the solver isolation boundary.

This is the only package permitted to import ``ortools`` (design rule 1). It
exposes ``Scheduler`` and ``FeasibilityChecker`` in domain terms, translating
at the boundary, which keeps the seam open for an alternative backend.

The working-time prefix relation of ADR-8 is materialised here, never in
``domain``: the domain owns ``Calendar`` as a predicate, ``solve`` turns it
into CP-SAT tables. Phase 2.
"""
