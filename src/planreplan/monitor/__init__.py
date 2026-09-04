"""Execution monitor: progress ingestion, state pinning, deviation detection.

Runs the cheap CPM float computation on every update and reaches for the
solver only when a deviation warrants it. All progress arithmetic happens in
working-tick space, never elapsed ticks. See ``docs/04-replanning-design.md``.
Phase 3.
"""
