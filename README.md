# planREplan

A construction planning and management tool built on a contrarian-but-old idea: the plan is not the product — the plan–monitor–repair loop is the product. planREplan pairs a deterministic constraint-solving core (CP-SAT over a resource-constrained scheduling model) with an execution monitor that detects deviations and repairs the schedule, quantifying the tradeoff between schedule stability and schedule optimality on every repair. An LLM front-end (strictly LLM-Modulo: translate and explain, never decide) arrives in a later phase.

## Status

Design phase. The `docs/` directory is the source of truth; implementation proceeds by the phases in `docs/06-roadmap.md`, built by Claude Code with a second Claude instance as design reviewer.

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

## Quickstart (will be true after Phase 0)

```
pip install -e ".[dev]"
pytest
planreplan --version
```
