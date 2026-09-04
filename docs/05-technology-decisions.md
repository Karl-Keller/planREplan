# 05 — Technology decisions

Recorded ADR-style: context, decision, consequences. Revisit deliberately, not by drift.

## ADR-1: Python 3.11+ as the implementation language

Maintainer's primary language; first-class OR-Tools and Anthropic SDK support; the performance-critical work happens inside CP-SAT's C++ core, so Python overhead is confined to model building. Consequence: mypy --strict on core packages to recover some of the safety a typed language would give.

## ADR-2: OR-Tools CP-SAT as the solver core (not a PDDL planner)

Context: the paradigm map in `docs/00-background.md` — construction planning is dominated by temporal/resource reasoning (RCPSP lineage), not action discovery (STRIPS lineage). The WBS gives us the actions; the problem is when and with what.

Decision: model scheduling and repair as CP-SAT problems using interval variables, `AddNoOverlap`/`AddCumulative` for resources, and precedence linear constraints. CP-SAT is free, mature, deterministic under fixed seed and worker count, handles the STABLE_RESOLVE objective naturally (soft constraints via penalty terms), and solves realistic instance sizes interactively.

Alternative considered: PDDL 2.1 temporal planning via `unified-planning`. Rejected as the core because durative-action planners are weaker on cumulative resources and offer no natural stability objective. Kept open as a seam: `Scheduler` is an interface, and a unified-planning adapter is a welcome experiment (it would also unlock plan-space niceties like landmark analysis). Consequence: solver isolation rule in `CLAUDE.md` is what keeps this seam real.

## ADR-3: pydantic v2 for the domain model

Validation at the boundary makes invalid states unrepresentable in the core, which the proposal gate (ADR-6) depends on. Frozen models give us immutable `Schedule` for free. Consequence: a thin conversion layer in `solve/` between pydantic objects and CP-SAT variables — acceptable, and it enforces the isolation boundary anyway.

## ADR-4: Files first (JSON + JSONL), SQLite when queries demand it

A project directory of versioned JSON plus an append-only event log is transparent, diffable, git-friendly, and sufficient for single-project v1. Consequence: a `SchemaVersion` field and migration policy from day one; the domain must not assume a storage engine.

## ADR-5: Typer CLI first; FastAPI service later; reports as self-contained HTML

The CLI exercises the full loop with minimal surface area and is the natural harness for Claude Code to build against. Self-contained HTML reports (inline SVG Gantt, before/after repair animations) require no server, attach to emails, and serve the maintainer's educational-documentation goals. Consequence: no web framework dependency until Phase 5+; report generation is a pure function of domain objects.

## ADR-6: LLM integration via the Anthropic API, strictly LLM-Modulo

The `llm/` package exposes exactly two capabilities: `parse(text) -> Proposal` (natural language to typed `ProgressReport` / `DisruptionEvent` / `ScopeChange` drafts) and `narrate(ChangeSet, ChurnMetrics) -> str` (solver output to plain-language explanation). Proposals are validated by pydantic, then checked by `FeasibilityChecker`, then surfaced to the human for confirmation — three gates before anything becomes project state. The LLM never selects repair policies, never edits schedules, never bypasses the solver. Model choice, prompt templates, and structured-output format are implementation details behind this interface; consult current Anthropic API docs (https://docs.claude.com) at build time rather than freezing them here. Consequence: the system is fully functional with `llm/` absent, which is also how it will be developed and tested for Phases 0–4.

## ADR-7: pytest + hypothesis; ruff for lint and format

Property-based testing fits this domain unusually well — CPM and repair invariants (see `04-replanning-design.md`) are algebraic laws over generated projects. A `ProjectStrategy` hypothesis generator (random DAGs with calendars and resources) is core test infrastructure, built in Phase 1 and reused everywhere. Determinism (fixed CP-SAT seed, `num_workers=1` in tests) keeps failures reproducible.
