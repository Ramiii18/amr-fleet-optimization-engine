"""Check that the engine still reproduces its published benchmark results.

    .venv\\Scripts\\python.exe scripts\\check_published_results.py [--seeds 3] [--lab] [--workers 8]

Re-runs the first seeds of the scaling benchmark and of the breakdown benchmark, and compares
every KPI of every run with the committed per-run results (``docs/results/*.csv``), value for
value as written. Columns that measure wall time are ignored: they depend on the machine.

With ``--lab`` it also re-runs the first seeds of every committed experiment-lab result
(``docs/results/lab_*.json``) from the experiment stored in the file, and compares every value of
every committed row. Fields added to rows since a file was written (such as ``map_hash``) are
not in the committed rows, so they are not compared.

A change that only observes the simulation (logging, traces) must leave every value as it was.
A difference here means the simulation itself changed. The exit code is 0 only if nothing
differs.
"""

import argparse
import csv
import dataclasses
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WALL_TIME = {"decisions", "decision_mean_ms"}
KEYS = ("orders_per_hour", "seed", "policy", "robots", "breakdowns_level", "recovery")
BENCHMARKS = (
    ("benchmark_scaling.py", "scaling.csv"),
    ("compare_breakdowns.py", "breakdowns.csv"),
)


def _rows(path: Path) -> dict[tuple[str, ...], dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as file:
        data = list(csv.DictReader(file))
    keys = [column for column in data[0] if column in KEYS]
    return {tuple(row[key] for key in keys): row for row in data}


def compare(committed: Path, fresh: Path) -> tuple[int, int, list[str]]:
    """How many runs and values were compared, and a line for each value that differs."""
    old, new = _rows(committed), _rows(fresh)
    values = 0
    differences = []
    for key, row in new.items():
        if key not in old:
            differences.append(f"{key}: not in {committed.name}")
            continue
        for column, value in row.items():
            if column in WALL_TIME:
                continue
            values += 1
            if old[key][column] != value:
                differences.append(f"{key} {column}: published {old[key][column]}, now {value}")
    return len(new), values, differences


def check_lab(seeds: int, workers: int) -> bool:
    """Re-run the committed lab results; True if anything differs."""
    from amr_optimizer.lab import experiment_from_dict, run_experiment
    from amr_optimizer.lab.runner import write_results

    failed = False
    for path in sorted((ROOT / "docs" / "results").glob("lab_*.json")):
        committed = json.loads(path.read_text(encoding="utf-8"))
        experiment = experiment_from_dict(committed["experiment"], base_dir=ROOT / "experiments")
        experiment = dataclasses.replace(experiment, seeds=experiment.seeds[:seeds])
        with tempfile.TemporaryDirectory() as folder:  # written as the lab writes them
            fresh_file = Path(folder) / "fresh.json"
            write_results(fresh_file, {"rows": run_experiment(experiment, workers=workers)})
            fresh = json.loads(fresh_file.read_text(encoding="utf-8"))["rows"]
        old = {(r["family"], r["seed"], r["policy"]): r for r in committed["rows"]}
        values, differences = 0, []
        for row in fresh:
            key = (row["family"], row["seed"], row["policy"])
            for column, value in old[key].items():
                values += 1
                if row.get(column) != value:
                    differences.append(f"{key} {column}: published {value}, now {row.get(column)}")
        print(
            f"{path.name}: {len(fresh)} runs, {values} values compared, {len(differences)} differ"
        )
        for line in differences[:20]:
            print(f"  {line}")
        failed = failed or bool(differences) or not fresh
    return failed


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--seeds", type=int, default=3, help="seeds 0 to N-1 of each benchmark")
    parser.add_argument("--lab", action="store_true", help="also re-run the lab's results")
    parser.add_argument("--workers", type=int, default=8, help="processes for the lab runs")
    parser.add_argument("--only-lab", action="store_true", help="skip the CSV benchmarks")
    args = parser.parse_args()
    failed = False
    if args.lab or args.only_lab:
        failed = check_lab(args.seeds, args.workers)
    if args.only_lab:
        return 1 if failed else 0
    with tempfile.TemporaryDirectory() as folder:
        for script, results in BENCHMARKS:
            fresh = Path(folder) / results
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / script),
                    "--seeds",
                    str(args.seeds),
                    "--csv",
                    str(fresh),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            runs, values, differences = compare(ROOT / "docs" / "results" / results, fresh)
            print(f"{results}: {runs} runs, {values} values compared, {len(differences)} differ")
            for line in differences[:20]:
                print(f"  {line}")
            failed = failed or bool(differences) or runs == 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
