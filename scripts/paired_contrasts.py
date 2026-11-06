"""Direct paired contrasts and recovery-fraction intervals from the committed per-run results.

No simulation is run: this reads ``docs/results/scaling.csv`` and ``docs/results/breakdowns.csv``.

- **Direct contrasts.** Earlier reports estimated some differences between two policies only as
  the difference of their leads over a common baseline. Here they are paired directly, per seed,
  with a 95% t interval: ``cpsat-fair`` minus ``insertion-fair`` (scaling), and ``replan`` minus
  ``retime`` (breakdowns), on mean completion time.
- **Recovery fractions.** The share of the cost of breakdowns a recovery policy recovers is a ratio
  of averages, R = (wait - recovery) / (wait - none). Its 95% interval comes from a paired
  bootstrap: seeds are resampled whole (wait, recovery and no-breakdown runs together), 10,000
  times, with a fixed random seed.

The intervals are per configuration and unadjusted for multiple comparisons: read them as
exploratory.

Usage (from the repository root):
    .venv\\Scripts\\python.exe scripts\\paired_contrasts.py
"""

import csv
import random
import statistics
from collections import defaultdict
from pathlib import Path

from amr_optimizer.metrics import paired_difference

ROOT = Path(__file__).resolve().parent.parent
RESAMPLES = 10_000


def _rows(name: str) -> list[dict[str, str]]:
    with (ROOT / "docs" / "results" / name).open(encoding="utf-8") as file:
        return list(csv.DictReader(file))


def _by_seed(rows: list[dict[str, str]], **match: str) -> dict[int, float]:
    return {
        int(r["seed"]): float(r["completion_mean_s"])
        for r in rows
        if all(r[key] == value for key, value in match.items())
    }


def main() -> None:
    scaling = _rows("scaling.csv")
    print("cpsat-fair minus insertion-fair, mean completion (scaling.csv, paired by seed):")
    print()
    print("| Robots | Orders/h | Seeds | Difference (95% CI) |")
    print("|---:|---:|---:|---|")
    configs = sorted({(int(r["robots"]), r["orders_per_hour"]) for r in scaling})
    for robots, rate in configs:
        a = _by_seed(scaling, robots=str(robots), orders_per_hour=rate, policy="cpsat-fair")
        b = _by_seed(scaling, robots=str(robots), orders_per_hour=rate, policy="insertion-fair")
        seeds = sorted(a)
        diff = paired_difference([a[s] for s in seeds], [b[s] for s in seeds])
        print(f"| {robots} | {rate} | {len(seeds)} | {diff.format()} |")

    breakdowns = _rows("breakdowns.csv")
    groups: dict[tuple[str, str, str], None] = defaultdict(lambda: None)
    for r in breakdowns:
        if r["breakdowns_level"] != "none":
            groups[(r["orders_per_hour"], r["policy"], r["breakdowns_level"])] = None
    rng = random.Random(0)
    print()
    print("replan minus retime, and recovery fractions with bootstrap intervals (breakdowns.csv):")
    print()
    print(
        "| Orders/h | Policy | Breakdowns | replan - retime (95% CI) "
        "| Recovered by retime (95% CI) | Recovered by replan (95% CI) |"
    )
    print("|---:|---|---|---|---|---|")
    for rate, policy, level in sorted(groups):
        base = {"orders_per_hour": rate, "policy": policy}
        none = _by_seed(breakdowns, **base, breakdowns_level="none")
        runs = {
            name: _by_seed(breakdowns, **base, breakdowns_level=level, recovery=name)
            for name in ("wait", "retime", "replan")
        }
        seeds = sorted(none)
        direct = paired_difference(
            [runs["replan"][s] for s in seeds], [runs["retime"][s] for s in seeds]
        )
        cells = []
        for name in ("retime", "replan"):

            def fraction(
                sample: list[int],
                name: str = name,
                runs: dict[str, dict[int, float]] = runs,
                none: dict[int, float] = none,
            ) -> float:
                wait = statistics.fmean(runs["wait"][s] for s in sample)
                rec = statistics.fmean(runs[name][s] for s in sample)
                ref = statistics.fmean(none[s] for s in sample)
                return (wait - rec) / (wait - ref)

            boot = sorted(fraction(rng.choices(seeds, k=len(seeds))) for _ in range(RESAMPLES))
            low, high = boot[int(0.025 * RESAMPLES)], boot[int(0.975 * RESAMPLES) - 1]
            cells.append(f"{fraction(seeds):.0%} [{low:.0%}, {high:.0%}]")
        print(f"| {rate} | {policy} | {level} | {direct.format()} | {cells[0]} | {cells[1]} |")


if __name__ == "__main__":
    main()
