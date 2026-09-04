# 06 — Roadmap

Phases are sized to be individually reviewable. Each ends with automated acceptance tests, clean lint/type checks, updated docs, and a CLI surface for the new capability (see "Definition of done" in `CLAUDE.md`). Later phases may reorder; earlier ones are load-bearing.

## Phase 0 — Scaffolding

Package layout per `CLAUDE.md`; pyproject with pinned dev tooling (pytest, hypothesis, ruff, mypy); CI workflow running lint + typecheck + tests; `planreplan --version` works. Acceptance: fresh clone → `pip install -e ".[dev]" && pytest` passes in under a minute.

## Phase 1 — Domain model + CPM engine

Implement `domain/` per `03-domain-model.md` with full validation; JSON load/save with schema version; the hypothesis `ProjectStrategy` generator; `cpm/` forward/backward pass, floats, critical path. CLI: `planreplan validate`, `planreplan cpm`.

Acceptance: property tests hold on generated projects — total float ≥ free float ≥ 0; zero total float ⟺ on a critical path; forward pass respects all four dependency kinds with lags; calendar round-trips (`toDate`/`toPeriod`) are inverse on workdays. Fixture: a hand-checked 12-task sample project (the inspection-delay scenario without the disruption) with published expected floats.

## Phase 2 — Resource-constrained scheduling

`solve/` CP-SAT scheduler: interval variables, cumulative resources, calendars via forbidden windows, makespan objective; `SolveOptions` (time limit, seed, workers); `FeasibilityChecker` validating any schedule against a project. CLI: `planreplan solve`, `planreplan check`.

Acceptance: on resource-unconstrained projects the solver's makespan equals CPM's; on the fixture with a capacity-1 crew shared by two parallel tasks, the solver serializes them; the checker rejects a schedule with a deliberately introduced overlap; two runs with the same seed produce identical schedules; a 500-task, 20-resource generated instance solves to feasibility within the default time limit.

## Phase 3 — Monitor + repair (the point of the project)

`monitor/`: event ingestion (progress, disruption), state pinning, deviation detection with the taxonomy and severity from `03`/`04`; `repair/`: RIGHT_SHIFT, STABLE_RESOLVE with the weighted-churn objective, FULL_RESOLVE, frozen zone, full `ChurnMetrics`. CLI: `planreplan ingest`, `planreplan status`, `planreplan repair --policy ...`.

Acceptance: the repair invariants in `04-replanning-design.md` §Testing pass as property tests; the inspection-delay fixture reproduces the canonical outcome — 2-day inspection slip, STABLE_RESOLVE recovers the original makespan by resequencing the gap-filling task while RIGHT_SHIFT loses 2 days, with `price_of_stability` = 0 and correct churn numbers.

## Phase 4 — Reporting

Self-contained HTML Gantt (inline SVG, no external assets); before/after repair view with a step-through of baseline → disruption → detection → repaired, in the style of the design-discussion animation; JSON diff export of ChangeSets. CLI: `planreplan report`.

Acceptance: reports open file:// with no network; golden-file tests on SVG structure for the fixture; a ChangeSet renders with every move listed and churn summary shown.

## Phase 5 — LLM front-end (LLM-Modulo)

`llm/` per ADR-6: `parse` to typed proposals, `narrate` for ChangeSets; CLI: `planreplan tell "<natural language>"` → shows the parsed proposal, validation/feasibility verdicts, and asks for confirmation; `planreplan explain` narrates the latest repair. Verify current Anthropic API capabilities and structured-output mechanisms against https://docs.claude.com at implementation time.

Acceptance: a corpus of ~20 realistic utterances ("inspector can't make Tuesday, earliest Thursday"; "pour finished today"; "add a task: temporary shoring, 3 days, after excavation") parses to correct proposals under test with a mocked API; malformed/hallucinated proposals are rejected at the gates with actionable errors; nothing in `llm/` is imported by core packages.

## Phase 6+ — Candidates, unordered

XER / MS Project XML import (FR8); resource leveling and cost objectives; multi-calendar refinements (weather calendars per trade); FastAPI service + minimal web UI; scenario comparison ("what if the steel is 2 weeks late?") as first-class workflow; probabilistic durations.
