# CLAUDE.md — planREplan

Instructions for Claude Code working in this repository. Read this file plus `docs/02-architecture.md` before writing code. When a task touches the replanning loop, also read `docs/04-replanning-design.md`.

## What this project is

planREplan is a general-purpose construction planning and **re**planning tool. The core thesis (see `docs/00-background.md`): in construction, the plan is never the product — the plan–monitor–repair loop is the product. The system is built around a deterministic solver core (CP-SAT over an RCPSP-style model) wrapped in an execution monitor that detects deviations and repairs the schedule with controllable stability (minimal churn vs. optimal makespan).

An LLM front-end is planned (Phase 5) but follows the LLM-Modulo pattern strictly: LLMs translate and explain; the symbolic solver validates and decides. **No LLM output ever enters the schedule without solver verification.**

## Tech stack

- Python 3.11+ (the maintainer's primary language)
- `pydantic` v2 for the domain model
- `ortools` (CP-SAT) for constrained scheduling
- `typer` for the CLI; `pytest` for tests; `ruff` for lint/format
- SQLite + JSON for persistence in early phases

## Repo layout

```
src/planreplan/
  domain/       # pydantic entities: Project, Task, Dependency, Resource, Calendar, Schedule...
  cpm/          # deterministic critical-path engine (forward/backward pass, float) — no solver deps
  solve/        # CP-SAT model builder + adapters; solver isolation boundary
  monitor/      # progress ingestion, deviation detection
  repair/       # repair policies: right-shift, stability re-solve, full re-solve
  io/           # JSON import/export; later XER/MSP XML
  llm/          # (Phase 5) Anthropic API adapter — translator/explainer only
  cli.py
tests/
docs/           # design documents — the source of truth for intent
```

## Commands

```
pip install -e ".[dev]"     # or: uv pip install -e ".[dev]"
pytest                      # run tests
pytest tests/test_repair.py -k churn   # focused run
ruff check src tests && ruff format --check src tests
```

## Design rules (hard constraints)

1. **Solver isolation.** Nothing outside `solve/` imports `ortools`. The domain model and CPM engine must run without OR-Tools installed. Adapters translate domain ↔ solver model at the boundary.
2. **Determinism in the core.** Given the same project state and random seed, `solve/`, `monitor/`, and `repair/` produce identical output. Set CP-SAT's `random_seed` and a fixed worker count in tests.
3. **The schedule is immutable data.** A repair produces a *new* `Schedule` plus a `ChangeSet` diff; it never mutates the old one. Baselines are frozen forever.
4. **Every repair reports churn.** Any function returning a repaired schedule also returns metrics: makespan delta, count of moved tasks, sum of |Δstart|, and whether the critical path changed. These are first-class outputs, not logging.
5. **Time is integer.** Model time as integer periods (workday index against a `Calendar`), never datetime arithmetic inside the solver. Convert at the io boundary.
6. **LLM outputs are proposals.** Anything from `llm/` is typed as a proposal object and must pass through domain validation + a solver feasibility check before it can touch a schedule. This rule is load-bearing; do not weaken it for convenience.

## Conventions

- Type hints everywhere; `mypy --strict` clean on `domain/` and `cpm/`.
- Tests first for `cpm/` and `repair/` — these have crisp mathematical specs (see acceptance criteria in `docs/06-roadmap.md`). Property-based tests with `hypothesis` are encouraged for CPM invariants (e.g., total float ≥ free float ≥ 0; critical tasks have zero total float).
- Docstrings explain *why*, referencing the design doc section when one exists.
- Keep functions on one abstraction level; the solver model builder is the only place allowed to be long.
- Mermaid diagrams in docs are normative. If code diverges from a diagram, either fix the code or update the diagram in the same PR — never let them drift silently.

## Definition of done for a phase

A phase (see `docs/06-roadmap.md`) is done when: all acceptance criteria pass as automated tests, `ruff` and `mypy` are clean, the relevant design doc is updated to match reality, and the CLI exposes the new capability with `--help` text.

## What to ask the maintainer about

Scope changes to the domain model, any new runtime dependency, weakening of design rules 1–6, and anything that changes the JSON schema of persisted projects (these are reviewed by a second Claude instance acting as design reviewer).
