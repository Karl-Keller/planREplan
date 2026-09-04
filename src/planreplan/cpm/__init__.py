"""Deterministic critical-path engine: forward pass, backward pass, floats.

Pure functions over domain objects with no solver dependency (design rule 1),
so CPM answers stay available when OR-Tools is absent and stay cheap enough
for the monitor to run on every progress update. Phase 1.
"""
