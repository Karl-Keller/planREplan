# 00 — Background: why a *re*planning tool

This document records the intellectual lineage behind planREplan and the argument for its central design decision: that the product is the plan–monitor–repair loop, not the plan.

## Three lineages, one system

Automated planning's history is usually told through two figures. Nils Nilsson (with Richard Fikes) built the foundational logic era at SRI: STRIPS (1971) gave us the precondition/effect action model that every modern planning representation, including PDDL, descends from, and the A* algorithm gave planning its search backbone. Hector Geffner (with Blai Bonet) opened the modern era with Heuristic Search Planning (1998), showing that domain-independent heuristics could be extracted automatically from the STRIPS representation itself, turning planning from brittle logical deduction into fast guided search.

A third figure matters more for this project, though he rarely makes the summaries. David Chapman's "Planning for Conjunctive Goals" (1987) both unified the partial-order planners of the 1970s into the rigorous TWEAK formalism and proved the negative results — undecidability of planning under expressive action representations, NP-hardness of truth verification with conditional effects — that define the ceiling classical planning operates under. Then, with Phil Agre, Chapman mounted the *situated action* critique: intelligent behavior is mostly moment-to-moment improvisation against a changing world, and plans function as resources for action, not as programs to be executed. The AI planning establishment treated this as heresy. Construction managers live it daily.

There is also a fourth lineage that grew up outside AI entirely: operations research. CPM and PERT (late 1950s) and the resource-constrained project scheduling problem (RCPSP) formalized exactly the questions a construction scheduler asks — precedence, durations, crews, cranes, makespan. Modern constraint-programming solvers (notably CP-SAT) solve industrial RCPSP instances that were intractable a decade ago. The AI-planning and OR-scheduling traditions have been converging, and planREplan deliberately sits at that convergence point.

## The construction argument

Construction is the canonical domain where the situated-action critique bites. A baseline schedule is famously obsolete within weeks: weather, RFIs, submittal delays, failed inspections, late deliveries, trade stacking, differing site conditions. The industry's standard response — a scheduler manually revising a P6 file, weeks behind reality — is precisely the "plan as program" failure mode Agre and Chapman diagnosed.

The design consequence is that replanning is not a feature bolted onto a planner; the planner is a subroutine of the replanner. Concretely:

1. The solver core is a resource-constrained *scheduler* (RCPSP via CP-SAT), not a STRIPS-style action planner. Construction's action model is largely known (the WBS); the hard part is time, resources, and disruption.
2. Hierarchical structure is native. HTN-style decomposition maps directly onto the WBS: "build foundation" decomposes into excavate → form → rebar → pour the way an HTN method does.
3. The execution monitor is a first-class subsystem, and repair is a spectrum from minimal-churn plan repair to full re-solve, chosen deliberately (see `04-replanning-design.md`). Stability versus optimality is the central tradeoff: a mathematically better schedule that churns every subcontractor's calendar is often a worse schedule.

## The neuro-symbolic layer

LLMs are demonstrably unreliable as autonomous long-horizon planners — hallucinated actions, constraint violations, unstable logic. But they are excellent translators. The field's answer is the LLM-Modulo pattern: the LLM converts messy natural language (specs, RFI emails, daily reports) into formal problem updates, and translates solver output back into human-readable narratives; a deterministic symbolic engine verifies and solves. planREplan adopts this pattern with a hard rule: LLM output is always a *proposal* that must pass domain validation and solver feasibility checks before touching a schedule.

## Key references

- Fikes, R. & Nilsson, N. (1971). STRIPS: A new approach to the application of theorem proving to problem solving. *Artificial Intelligence* 2.
- Chapman, D. (1987). Planning for conjunctive goals. *Artificial Intelligence* 32.
- Agre, P. & Chapman, D. (1987). Pengi: An implementation of a theory of activity. *AAAI-87*; and Agre & Chapman (1990), What are plans for? *Robotics and Autonomous Systems* 6.
- Bonet, B. & Geffner, H. (2001). Planning as heuristic search. *Artificial Intelligence* 129.
- Ghallab, M., Nau, D. & Traverso, P. (2016). *Automated Planning and Acting*. Cambridge UP. (The standard text arguing planning and acting must be designed together.)
- Kambhampati, S. et al. (2024). LLMs can't plan, but can help planning in LLM-Modulo frameworks. *ICML 2024*.
- Artigues, C., Demassey, S. & Néron, E. (eds.) (2008). *Resource-Constrained Project Scheduling*. Wiley-ISTE.
