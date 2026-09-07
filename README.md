# planREplan

A construction planning and management tool built on a contrarian-but-old idea: the plan is not the product — the plan–monitor–repair loop is the product. planREplan pairs a deterministic constraint-solving core (CP-SAT over a resource-constrained scheduling model) with an execution monitor that detects deviations and repairs the schedule, quantifying the tradeoff between schedule stability and schedule optimality on every repair. An LLM front-end (strictly LLM-Modulo: translate and explain, never decide) arrives in a later phase.

## Status

Phases 0 (scaffolding), 1 (domain model, calendars, overtime, persistence, CPM) and 2 (CP-SAT scheduling, schedules, verification) are complete; Phase 3 (monitor and repair — the point of the project) is next. `validate`, `cpm`, `overtime`, `solve`, and `check` work today. The `docs/` directory is the source of truth; implementation proceeds by the phases in `docs/06-roadmap.md`, built by Claude Code with a second Claude instance as design reviewer.

## Documents

| Doc | What it covers |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Instructions and hard design rules for Claude Code |
| [`docs/00-background.md`](docs/00-background.md) | Intellectual lineage: STRIPS to heuristic search to situated action to neuro-symbolic; why construction demands a *re*planner |
| [`docs/01-requirements.md`](docs/01-requirements.md) | Vision, users, functional and non-functional requirements, non-goals |
| [`docs/02-architecture.md`](docs/02-architecture.md) | Layered architecture, solver isolation, the proposal gate; component and class diagrams |
| [`docs/03-domain-model.md`](docs/03-domain-model.md) | Entities, semantics, invariants, validation, persistence |
| [`docs/04-replanning-design.md`](docs/04-replanning-design.md) | The loop itself: frozen zone, repair policies, churn metrics, testing invariants |
| [`docs/05-technology-decisions.md`](docs/05-technology-decisions.md) | ADRs: Python, CP-SAT, pydantic, files-first storage, Typer, LLM-Modulo |
| [`docs/06-roadmap.md`](docs/06-roadmap.md) | Build phases with acceptance criteria |

Diagrams are Mermaid and render directly on GitHub.

## Quickstart

```
pip install -e ".[dev]"
pytest
planreplan --version
```

Lint, format, and typecheck the way CI does:

```
ruff check src tests && ruff format --check src tests && mypy
```

`ortools` is an optional dependency rather than a core one, because design
rule 1 requires the domain model and CPM engine to run without it — a claim
only worth making if a plain `pip install -e .` genuinely lacks the solver.
`tests/test_architecture.py` enforces that and the other hard design rules
executably, rather than leaving them as prose to be noticed in review.
