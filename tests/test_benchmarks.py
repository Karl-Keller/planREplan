"""Keep the published benchmark runnable.

The scale numbers in ``docs/06-roadmap.md`` were once produced by a throwaway
script that no longer existed, which made them unreproducible and therefore
unfalsifiable. The script is committed now; this runs it at toy scale so it
cannot rot silently while the numbers it produced stay in a document.

Not a performance test. It asserts the measurement still *works*, never how
fast it is — timing assertions on a shared runner are noise.
"""

import subprocess
import sys
from pathlib import Path

import pytest

BENCHMARK = Path(__file__).resolve().parents[1] / "benchmarks" / "rcpsp_scale.py"

pytest.importorskip("ortools", reason="the benchmark solves; a core install cannot")

from benchmarks.rcpsp_scale import make_instance, measure  # noqa: E402

from planreplan.domain import validate_project  # noqa: E402


@pytest.mark.parametrize("ticks_per_day", [1, 24])
def test_the_generated_instance_is_valid(ticks_per_day):
    project = make_instance(tasks=12, resources=3, ticks_per_day=ticks_per_day)
    index = validate_project(project)
    assert len(index.leaves) == 12


def test_the_instance_is_reproducible_from_its_seed():
    """Wall times vary by machine; the instance must not."""
    first = make_instance(tasks=12, resources=3, ticks_per_day=24, seed=11)
    second = make_instance(tasks=12, resources=3, ticks_per_day=24, seed=11)
    assert first == second
    assert make_instance(tasks=12, resources=3, ticks_per_day=24, seed=12) != first


def test_a_measurement_reports_every_column_the_roadmap_publishes():
    project = make_instance(tasks=12, resources=3, ticks_per_day=24)
    result = measure("toy", project, seconds=2)
    assert result.fitted_horizon >= result.validated_horizon
    assert result.greedy_makespan > 0
    assert result.status in {"OPTIMAL", "FEASIBLE", "UNKNOWN"}
    assert result.row().startswith("| toy |")


def test_the_script_runs_as_a_command():
    """The way the roadmap tells a reader to reproduce the numbers."""
    finished = subprocess.run(
        [sys.executable, str(BENCHMARK), "--tasks", "12", "--resources", "3", "--seconds", "1"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    assert "| daily" in finished.stdout
    assert "| hourly" in finished.stdout
