# 05 — Technology decisions

Recorded ADR-style: context, decision, consequences. Revisit deliberately, not by drift.

## ADR-1: Python 3.11+ as the implementation language

Maintainer's primary language; first-class OR-Tools and Anthropic SDK support; the performance-critical work happens inside CP-SAT's C++ core, so Python overhead is confined to model building. Consequence: mypy --strict on core packages to recover some of the safety a typed language would give.

## ADR-2: OR-Tools CP-SAT as the solver core (not a PDDL planner)

Context: the paradigm map in `docs/00-background.md` — construction planning is dominated by temporal/resource reasoning (RCPSP lineage), not action discovery (STRIPS lineage). The WBS gives us the actions; the problem is when and with what.

Decision: model scheduling and repair as CP-SAT problems using interval variables, `AddNoOverlap`/`AddCumulative` for resources, and precedence linear constraints. CP-SAT is free, mature, deterministic under fixed seed and worker count, handles the STABLE_RESOLVE objective naturally (soft constraints via penalty terms), and solves realistic instance sizes interactively.

Alternative considered: PDDL 2.1 temporal planning via `unified-planning`. Rejected as the core because durative-action planners are weaker on cumulative resources and offer no natural stability objective. Kept open as a seam: `Scheduler` is an interface, and a unified-planning adapter is a welcome experiment (it would also unlock plan-space niceties like landmark analysis). Consequence: the solver isolation rule in `CLAUDE.md` is what keeps this seam real, and `ortools` is packaged as an optional dependency (`.[solve]`) rather than a core one so that the claim is falsifiable — a core install that accidentally required OR-Tools would fail CI rather than pass unnoticed.

## ADR-3: pydantic v2 for the domain model

Validation at the boundary makes invalid states unrepresentable in the core, which the proposal gate (ADR-6) depends on. Frozen models give us immutable `Schedule` for free. Consequence: a thin conversion layer in `solve/` between pydantic objects and CP-SAT variables — acceptable, and it enforces the isolation boundary anyway.

## ADR-4: Files first (JSON + JSONL), SQLite when queries demand it

A project directory of versioned JSON plus an append-only event log is transparent, diffable, git-friendly, and sufficient for single-project v1. Consequence: a `SchemaVersion` field and migration policy from day one; the domain must not assume a storage engine. The policy is strict refusal in both directions — a newer file may carry fields whose meaning this build does not know, and an older one names the migration nobody has written yet. Guessing either way would corrupt a schedule silently, which is worse than declining to open it.

## ADR-5: Typer CLI first; FastAPI service later; reports as self-contained HTML

The CLI exercises the full loop with minimal surface area and is the natural harness for Claude Code to build against. Self-contained HTML reports (inline SVG Gantt, before/after repair animations) require no server, attach to emails, and serve the maintainer's educational-documentation goals. Consequence: no web framework dependency until Phase 5+; report generation is a pure function of domain objects.

## ADR-6: LLM integration via the Anthropic API, strictly LLM-Modulo

The `llm/` package exposes exactly two capabilities: `parse(text) -> Proposal` (natural language to typed `ProgressReport` / `DisruptionEvent` / `ScopeChange` drafts) and `narrate(ChangeSet, ChurnMetrics) -> str` (solver output to plain-language explanation). Proposals are validated by pydantic, then checked by `FeasibilityChecker`, then surfaced to the human for confirmation — three gates before anything becomes project state. The LLM never selects repair policies, never edits schedules, never bypasses the solver. Model choice, prompt templates, and structured-output format are implementation details behind this interface; consult current Anthropic API docs (https://docs.claude.com) at build time rather than freezing them here. Consequence: the system is fully functional with `llm/` absent, which is also how it will be developed and tested for Phases 0–4.

## ADR-7: pytest + hypothesis; ruff for lint and format

Property-based testing fits this domain unusually well — CPM and repair invariants (see `04-replanning-design.md`) are algebraic laws over generated projects. A `ProjectStrategy` hypothesis generator (random DAGs with calendars and resources) is core test infrastructure, built in Phase 1 and reused everywhere. Determinism (fixed CP-SAT seed, `num_workers=1` in tests) keeps failures reproducible.

## ADR-8: A uniform integer tick axis with calendars as predicates

Context: `03-domain-model.md` originally defined time as "an integer working-period index" — a per-calendar axis with non-working time compressed out. That is unworkable once more than one calendar exists, because two calendars produce two incompatible axes and no consistent way to state a precedence or resource constraint across them. It is also fatal to overtime: compressing non-working time out of the axis makes Saturday unnameable, and overtime is defined precisely by *where in the week* work landed.

Decision: the axis is uniform, calendar-agnostic integer ticks from a timezone-aware project epoch, at a resolution declared per project (`ticksPerDay`, default 24). Calendars are predicates over that axis. Task duration is work content in working ticks; the span a task occupies is derived and start-dependent.

The apparent cost is that a task's size is no longer constant, which CP-SAT cannot express directly. It is recovered with the **working-time prefix relation**: for each calendar, precompute `W(t)` = the number of working ticks in `[0, t)` and post it as an `AddAllowedAssignments` table over `(tick, workIndex)` pairs for working ticks only. A task is then three linear constraints — `w_last == w_start + duration - 1`, with `start` and `last` each related to their work index through the table. The table is keyed by **calendar**, not by (calendar, duration), so one table per distinct effective calendar is shared across every task using it; a five-day eight-hour calendar over two years is roughly 4,200 rows. This gives compressed working-time arithmetic inside the solver while the uniform axis survives outside it.

Alternative considered: a per-calendar compressed axis with conversion functions at every cross-calendar constraint. Rejected — the conversions are themselves monotone step relations of the same size, so it costs the same and loses the ability to name non-working time.

The domain-side representation is sorted runs of working ticks rather than a dense array: affine within a run, O(log n) to query, closed under the intersection that effective calendars require, and small enough that caching one index per distinct resource mix is free. Consequences: hourly resolution multiplies the time dimension by 24 against a daily model, which puts NFR2 at risk; `ticksPerDay` is therefore a genuine tuning knob and coarsening is the documented escape hatch. Daylight-saving transitions become real at sub-day resolution and are absorbed by `Calendar` at the local-shift-to-tick conversion, keeping all solver arithmetic uniform. Design rule 5 in `CLAUDE.md` is restated: integer *ticks against an epoch*, not workday indices.
