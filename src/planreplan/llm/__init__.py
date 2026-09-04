"""Anthropic API adapter — translator and explainer only.

Strictly LLM-Modulo (ADR-6): ``parse`` produces typed proposals and ``narrate``
turns a ChangeSet into prose. Nothing here ever selects a repair policy, edits
a schedule, or bypasses the solver, and no core package imports this one
(design rule 6). Phase 5.
"""
