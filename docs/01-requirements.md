# 01 — Product vision and requirements

## Vision

A general-purpose construction planning and management tool whose defining capability is *continuous replanning*: when reality diverges from the schedule, the system detects the deviation, repairs the schedule with a controllable balance of stability and optimality, and explains what changed and why. It should be useful to a single scheduler on day one (CLI + files) and grow toward a service with an LLM conversational front-end.

## Users

The primary user is a project scheduler or project engineer on a commercial construction project who today maintains the schedule in P6 or Microsoft Project and updates it manually. Secondary users are superintendents and PMs who consume schedule outputs (lookaheads, delay narratives) and, later, feed progress in via natural language. The maintainer is also a user: the tool doubles as an educational codebase, so clarity and explanatory documentation are requirements, not niceties.

## Functional requirements

FR1 — Project modeling. Represent a project as a hierarchical WBS of tasks with durations, precedence dependencies (FS, SS, FF, SF, each with optional lag), renewable resources with capacities (crews, equipment), per-resource and per-project calendars, and milestones with optional deadlines.

FR2 — Deterministic CPM. Compute early/late start/finish, total and free float, and the critical path via forward/backward pass, independent of any solver, for any project without resource constraints.

FR3 — Resource-constrained scheduling. Produce a feasible schedule respecting precedence, calendars, and resource capacities, minimizing makespan (later: alternative objectives such as resource leveling and cost), via CP-SAT.

FR4 — Baselines and progress. Freeze any schedule as a named baseline. Ingest progress updates (percent complete, actual start/finish, remaining duration) and disruption events (resource unavailability windows, scope additions, duration changes).

FR5 — Deviation detection. Given current state and the active schedule, detect and classify deviations: slipped starts/finishes, remaining-duration growth, precedence violations, resource conflicts, deadline jeopardy (float exhaustion trends), and invalid plans.

FR6 — Repair. Offer at least three repair strategies — right-shift propagation, stability-constrained re-solve (minimize weighted start-time perturbation subject to a makespan bound), and full re-solve — each returning a new schedule plus a ChangeSet and churn metrics (makespan delta, tasks moved, Σ|Δstart|, critical-path change).

FR7 — Reporting. Export schedules and diffs as JSON; render Gantt views and repair before/after comparisons as self-contained HTML (animation-friendly, usable in downstream educational docs).

FR8 — Interoperability (later phase). Import from Primavera XER and MS Project XML; export back where lossless.

FR9 — LLM front-end (later phase). Translate natural-language inputs (daily reports, RFI emails, "the inspector can't come until Thursday") into typed proposal objects; translate ChangeSets into narrative explanations. All proposals require solver-verified acceptance.

## Non-functional requirements

NFR1 — Determinism and reproducibility in the core (fixed seeds ⇒ identical outputs); NFR2 — solve/repair on a 500-task, 20-resource project completes interactively (target < 10 s on a laptop, with a documented time-limit/quality knob); NFR3 — the domain model and CPM engine have zero solver dependencies; NFR4 — every schedule mutation is auditable via immutable schedules + ChangeSets; NFR5 — test coverage: CPM and repair invariants are property-tested; NFR6 — runs fully offline except the optional LLM adapter.

## Explicit non-goals (v1)

Cost loading and earned-value management beyond basic fields; 4D/BIM integration; multi-project resource pools; probabilistic/Monte-Carlo scheduling (design should not preclude it — duration distributions are a plausible v2); autonomous LLM planning of any kind, permanently.
