# 01 — Product vision and requirements

## Vision

A general-purpose construction planning and management tool whose defining capability is *continuous replanning*: when reality diverges from the schedule, the system detects the deviation, repairs the schedule with a controllable balance of stability and optimality, and explains what changed and why. It should be useful to a single scheduler on day one (CLI + files) and grow toward a service with an LLM conversational front-end.

## Users

The primary user is a project scheduler or project engineer on a commercial construction project who today maintains the schedule in P6 or Microsoft Project and updates it manually. Secondary users are superintendents and PMs who consume schedule outputs (lookaheads, delay narratives) and, later, feed progress in via natural language. The maintainer is also a user: the tool doubles as an educational codebase, so clarity and explanatory documentation are requirements, not niceties.

## Functional requirements

FR1 — Project modeling. Represent a project as a hierarchical WBS of tasks with durations, precedence dependencies (FS, SS, FF, SF, each with optional lag), renewable resources with capacities (crews, equipment), per-resource and per-project calendars supporting arbitrary work weeks (five-day, seven-day, compressed 4x10) and named shifts, and milestones with optional deadlines. Time is a uniform integer tick axis at a project-declared resolution, hourly by default (see ADR-8).

FR2 — Deterministic CPM. Compute early/late start/finish, total and free float, and the critical path via forward/backward pass, independent of any solver, for any project without resource constraints.

FR3 — Resource-constrained scheduling. Produce a feasible schedule respecting precedence, calendars, and resource capacities, minimizing makespan (later: alternative objectives such as resource leveling and cost), via CP-SAT.

FR4 — Baselines and progress. Freeze any schedule as a named baseline. Ingest progress updates (actual start/finish and remaining duration, which is authoritative; percent complete is accepted as input and reconciled at the boundary) and disruption events (calendar exceptions withdrawing a resource's availability, scope additions, duration changes).

FR5 — Deviation detection. Given current state and the active schedule, detect and classify deviations: slipped starts/finishes, remaining-duration growth, precedence violations, resource conflicts, deadline jeopardy (float exhaustion trends), and invalid plans. Each carries a derived severity as an ordered (band, magnitude) pair, defined without division by float so that critical tasks rank correctly.

FR6 — Repair. Offer at least three repair strategies — right-shift propagation, stability-constrained re-solve (minimize weighted start-time perturbation subject to a makespan bound), and full re-solve — each returning a new schedule plus a ChangeSet (moves carrying both Δstart and Δfinish, plus scope additions, removals, and duration changes) and churn metrics (makespan delta, tasks moved, Σ|Δstart|, critical-path change).

FR7a — Overtime representation. Model labor agreements per resource (daily and weekly hour thresholds, day-of-week and shift premiums, holiday premiums) and report, for any schedule, the regular and premium hours each resource accrues per pay week. v1 represents and reports overtime; optimizing against it is a later phase.

FR7 — Reporting. Export schedules and diffs as JSON; render Gantt views and repair before/after comparisons as self-contained HTML (animation-friendly, usable in downstream educational docs).

FR8 — Interoperability (later phase). Import from Primavera XER and MS Project XML; export back where lossless.

FR9 — LLM front-end (later phase). Translate natural-language inputs (daily reports, RFI emails, "the inspector can't come until Thursday") into typed proposal objects; translate ChangeSets into narrative explanations. All proposals require solver-verified acceptance.

## Non-functional requirements

NFR1 — Determinism and reproducibility in the core (fixed seeds ⇒ identical outputs); NFR2 — solve/repair on a 500-task, 20-resource project completes interactively (target < 10 s on a laptop at daily resolution, with a documented time-limit/quality knob; hourly resolution multiplies the time dimension by 24, so its performance is measured and published in Phase 2 rather than promised here, and `ticksPerDay` is the documented coarsening knob); NFR3 — the domain model and CPM engine have zero solver dependencies; NFR4 — every schedule mutation is auditable via immutable schedules + ChangeSets; NFR5 — test coverage: CPM and repair invariants are property-tested; NFR6 — runs fully offline except the optional LLM adapter.

## Explicit non-goals (v1)

Cost loading and earned-value management beyond basic fields; 4D/BIM integration; multi-project resource pools; probabilistic/Monte-Carlo scheduling (design should not preclude it — duration distributions are a plausible v2); autonomous LLM planning of any kind, permanently.
