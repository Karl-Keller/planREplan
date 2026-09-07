"""Reproduce the scale measurement published in ``docs/06-roadmap.md``.

Run it::

    python benchmarks/rcpsp_scale.py            # the published 500/20 instance
    python benchmarks/rcpsp_scale.py --tasks 50 # something quicker

The output is a markdown table meant to be pasted straight into the roadmap,
so the published numbers and the script that made them cannot drift apart.
Wall times are machine-dependent; the instance, horizons, and makespans are
not — the generator is seeded, so the same seed gives the same project.

Not run by CI: it takes tens of seconds and measures a laptop. ``tests/
test_benchmarks.py`` runs it at toy scale instead, so the script cannot rot
unnoticed while the numbers it produced stay in a document.
"""

from __future__ import annotations

import argparse
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo

from planreplan.domain import (
    Assignment,
    Calendar,
    DayOfWeek,
    Dependency,
    Project,
    Resource,
    Shift,
    Task,
    TimeAxis,
    validate_project,
)
from planreplan.solve import CpSatScheduler, SolveOptions, fitted_index

NY = ZoneInfo("America/New_York")

#: How far back a dependency may reach. Small enough to leave parallelism,
#: large enough that the network is not a single chain.
_LOOKBACK = 12


def make_instance(*, tasks: int, resources: int, ticks_per_day: int, seed: int = 7) -> Project:
    """A seeded RCPSP instance: layered precedence, contended resources."""
    rng = random.Random(seed)
    zone: tzinfo
    if ticks_per_day >= 24:
        shift = (Shift(name="day", start_minute=8 * 60, end_minute=16 * 60),)
        duration = (4, 24)
        # A daylight-saving zone, which only hourly-or-finer ticks can express.
        zone = NY
    else:
        shift = (Shift(name="day", start_minute=0, end_minute=1440),)
        duration = (1, 3)
        # A daily tick cannot exist where the clock changes: a local day is
        # then 23 or 25 hours and stops landing on the grid. See ADR-8.
        zone = UTC

    calendar = Calendar(id="c", week_pattern=dict.fromkeys(tuple(DayOfWeek)[:5], shift))
    task_list = [Task(id=f"t{n}", duration=rng.randint(*duration)) for n in range(tasks)]
    links: list[Dependency] = []
    for n in range(tasks):
        for _ in range(rng.choice([0, 1, 1, 2])):
            if n:
                earlier = rng.randint(max(0, n - _LOOKBACK), n - 1)
                links.append(Dependency(predecessor_id=f"t{earlier}", successor_id=f"t{n}"))

    pool = [Resource(id=f"r{k}", capacity=rng.randint(2, 6)) for k in range(resources)]
    assignments = [
        Assignment(task_id=f"t{n}", resource_id=f"r{k}", demand=rng.randint(1, pool[k].capacity))
        for n in range(tasks)
        for k in rng.sample(range(resources), rng.randint(1, 2))
    ]

    return Project(
        id="benchmark",
        axis=TimeAxis(epoch=datetime(2026, 1, 5, tzinfo=zone), ticks_per_day=ticks_per_day),
        calendars=(calendar,),
        default_calendar_id="c",
        tasks=tuple(task_list),
        dependencies=tuple(dict.fromkeys(links)),
        resources=tuple(pool),
        assignments=tuple(assignments),
    )


@dataclass(frozen=True, slots=True)
class Measurement:
    label: str
    validated_horizon: int
    validate_seconds: float
    fitted_horizon: int
    greedy_makespan: int
    fit_seconds: float
    status: str
    makespan: int
    gap: int
    solve_seconds: float

    def row(self) -> str:
        return (
            f"| {self.label} | {self.validated_horizon} | {self.validate_seconds:.2f} s "
            f"| {self.fitted_horizon} | {self.greedy_makespan} ticks in "
            f"{self.fit_seconds:.2f} s | {self.makespan} ticks, gap {self.gap} "
            f"| {self.status} |"
        )


def measure(label: str, project: Project, *, seconds: float) -> Measurement:
    start = time.perf_counter()
    index = validate_project(project)
    validated = time.perf_counter() - start

    start = time.perf_counter()
    fitted, greedy = fitted_index(index)
    fit = time.perf_counter() - start

    start = time.perf_counter()
    result = CpSatScheduler(SolveOptions(time_limit_seconds=seconds)).solve(fitted)
    solved = time.perf_counter() - start

    return Measurement(
        label=label,
        validated_horizon=index.horizon,
        validate_seconds=validated,
        fitted_horizon=fitted.horizon,
        greedy_makespan=greedy.project_finish,
        fit_seconds=fit,
        status=result.status,
        makespan=result.schedule.project_finish,
        gap=result.gap,
        solve_seconds=solved,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=500)
    parser.add_argument("--resources", type=int, default=20)
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    print(
        f"{args.tasks} tasks, {args.resources} resources, "
        f"{args.seconds:g}-second limit, seed {args.seed}\n"
    )
    print("| | validated horizon | validate | fitted horizon | greedy | CP-SAT | status |")
    print("|---|---|---|---|---|---|---|")
    for label, ticks_per_day in (("daily (`ticksPerDay=1`)", 1), ("hourly (`ticksPerDay=24`)", 24)):
        project = make_instance(
            tasks=args.tasks,
            resources=args.resources,
            ticks_per_day=ticks_per_day,
            seed=args.seed,
        )
        print(measure(label, project, seconds=args.seconds).row(), flush=True)


if __name__ == "__main__":
    main()
