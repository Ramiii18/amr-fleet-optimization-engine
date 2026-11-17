"""Secondary analysis of the layout study: does the proxy rank the layouts as the simulation does?

    .venv\\Scripts\\python.exe scripts\\layout_study_tau.py docs\\results\\layout_study.json ^
        experiments\\layout_study\\candidates\\screening.json

As pre-registered (`docs/LAYOUT_STUDY.md`, "Secondary and exploratory analyses"): for each family
and policy, Kendall's tau-b between the static ranking of the layouts (the station round-trip
proxy, lower is better) and the simulated ranking (mean completion time over the seeds, lower is
better), with a 95% percentile interval from 10,000 bootstrap resamples of whole seeds (the same
resampled seeds for every layout; generator seed 0). With five layouts tau takes few values; it is
descriptive. Prints Markdown.
"""

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

from scipy.stats import kendalltau

RESAMPLES = 10_000
KPI = "completion_mean_s"


def tau_b(x: list[float], y: list[float]) -> float:
    return float(kendalltau(x, y, variant="b").statistic)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("results", type=Path)
    parser.add_argument("screening", type=Path)
    args = parser.parse_args()
    results = json.loads(args.results.read_text(encoding="utf-8"))
    screening = json.loads(args.screening.read_text(encoding="utf-8"))
    proxy = {row["name"]: row["station_round_trip_m"] for row in screening["candidates"]}
    experiment = results["experiment"]
    layouts = [layout["name"] for layout in experiment["layouts"]]
    seeds = experiment["seeds"]
    value = {
        (r["family"], r["policy"], r["layout"], r["seed"]): float(r[KPI]) for r in results["rows"]
    }
    print(
        "| Family | Policy | Layouts by proxy (best first) | Layouts by simulation (best first) "
        "| tau-b | 95% bootstrap interval |"
    )
    print("|---|---|---|---|---:|---|")
    for family in (f["name"] for f in experiment["families"]):
        for policy in experiment["policies"]:
            means = {
                name: statistics.fmean(value[family, policy, name, s] for s in seeds)
                for name in layouts
            }
            static = [proxy[name] for name in layouts]
            observed = tau_b(static, [means[name] for name in layouts])
            rng = random.Random(0)
            draws = []
            for _ in range(RESAMPLES):
                sample = [rng.choice(seeds) for _ in seeds]
                resampled = [
                    statistics.fmean(value[family, policy, name, s] for s in sample)
                    for name in layouts
                ]
                draws.append(tau_b(static, resampled))
            draws.sort()
            low, high = draws[int(0.025 * RESAMPLES)], draws[int(0.975 * RESAMPLES) - 1]
            by_proxy = " < ".join(sorted(layouts, key=lambda n: proxy[n]))
            by_sim = " < ".join(sorted(layouts, key=lambda n: means[n]))
            print(
                f"| {family} | {policy} | {by_proxy} | {by_sim} | {observed:+.2f} "
                f"| [{low:+.2f}, {high:+.2f}] |"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
