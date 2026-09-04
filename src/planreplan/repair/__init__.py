"""Repair policies and churn metrics — the point of the project.

RIGHT_SHIFT, STABLE_RESOLVE, FULL_RESOLVE. Every repair returns a new
``Schedule`` plus a ``ChangeSet`` and ``ChurnMetrics`` (design rules 3 and 4),
and never surfaces a bare INFEASIBLE: see the relaxation ladder in
``docs/04-replanning-design.md``. Phase 3.
"""
