# 02 — Architecture

## Shape of the system

planREplan is a layered system with a deterministic symbolic core and optional intelligent periphery. Dependencies point strictly inward: the periphery knows the core; the core knows nothing of the periphery.

```mermaid
flowchart TB
  subgraph periphery["Periphery (adapters)"]
    CLI[cli — Typer]
    IO[io — JSON, XER/MSP]
    LLM[llm — Anthropic adapter, Phase 5]
    RPT[reports — Gantt HTML, diffs]
  end
  subgraph loop["Replanning loop"]
    MON[monitor — deviation detection]
    REP[repair — policies + churn metrics]
  end
  subgraph core["Deterministic core"]
    DOM[domain — pydantic entities]
    CPM[cpm — critical path engine]
    SLV[solve — CP-SAT adapter]
  end
  CLI --> MON
  CLI --> SLV
  CLI --> RPT
  IO --> DOM
  LLM -->|typed proposals only| MON
  MON --> REP
  REP --> SLV
  SLV --> DOM
  CPM --> DOM
  MON --> DOM
  RPT --> DOM
```

The load-bearing boundaries:

1. **Solver isolation.** Only `solve/` imports `ortools`. It exposes `Scheduler` (build model → solve → extract `Schedule`) and `FeasibilityChecker` (validate a schedule or proposal against constraints). Everything else works with domain objects. This keeps the domain testable without OR-Tools, and keeps the seam open for an alternative backend (e.g., a PDDL/unified-planning adapter) without touching callers.
2. **The proposal gate.** `llm/` can only emit typed proposal objects (`ProgressReport`, `DisruptionEvent`, `ScopeChange` drafts). These pass through domain validation, then `FeasibilityChecker`, before the monitor treats them as facts. The LLM-Modulo pattern as a type system.
3. **Immutable schedules.** `Schedule` values are frozen; `repair/` returns `(new_schedule, change_set, churn_metrics)`. History is a sequence of schedules linked by ChangeSets — an audit trail by construction.

## Class-level view

```mermaid
classDiagram
  direction TB
  class Scheduler {
    <<interface>>
    +solve(Project, SolveOptions) Schedule
  }
  class FeasibilityChecker {
    <<interface>>
    +check(Project, Schedule) list~Violation~
  }
  class CpSatScheduler {
    -buildModel(Project) CpModel
    +solve(Project, SolveOptions) Schedule
  }
  class CpmEngine {
    +forwardPass(Project) EarlyDates
    +backwardPass(Project) LateDates
    +floats(Project) FloatReport
    +criticalPath(Project) list~TaskId~
  }
  class ExecutionMonitor {
    +ingest(ProgressReport)
    +ingest(DisruptionEvent)
    +detect(Project, Schedule) list~Deviation~
  }
  class RepairEngine {
    +repair(Project, Schedule, list~Deviation~, RepairPolicy) RepairResult
  }
  class RepairPolicy {
    <<enumeration>>
    RIGHT_SHIFT
    STABLE_RESOLVE
    FULL_RESOLVE
  }
  class RepairResult {
    +schedule Schedule
    +changes ChangeSet
    +churn ChurnMetrics
  }
  class LlmFrontEnd {
    +parse(text) Proposal
    +narrate(ChangeSet) str
  }
  Scheduler <|.. CpSatScheduler
  FeasibilityChecker <|.. CpSatScheduler
  RepairEngine --> Scheduler : re-solves via
  RepairEngine --> RepairPolicy
  RepairEngine --> RepairResult : returns
  ExecutionMonitor --> CpmEngine : float trends
  ExecutionMonitor ..> RepairEngine : hands deviations to
  LlmFrontEnd ..> ExecutionMonitor : proposals (gated)
```

Notes for implementers: `CpmEngine` is pure functions over the domain — no classes needed beyond a namespace if that's cleaner in Python. `ExecutionMonitor` consults CPM float trends to flag *deadline jeopardy* before anything is formally late; this is the cheap early-warning path that doesn't require a solver call. `RepairEngine` is where the stability/optimality tradeoff lives — see `04-replanning-design.md` for the policy semantics and the objective function of `STABLE_RESOLVE`.

## Interfaces and phasing

Phase 1–4 expose everything through the CLI (`planreplan solve`, `status`, `repair`, `report`) operating on a project directory (SQLite db + JSON import/export). A FastAPI service wrapping the same core is a Phase 5+ concern and must not leak into core design. Reports are self-contained HTML files (inline SVG Gantt, before/after repair views) so they can be attached to emails and embedded in educational docs without a server.
