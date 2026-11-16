"""Run an experiment file and print its analysis.

.venv\\Scripts\\python.exe -m amr_optimizer.lab experiments\\dispatch.json --out results.json
"""

import argparse
import dataclasses
import subprocess
import sys
from pathlib import Path

from amr_optimizer.lab.experiment import MODELS, ExperimentValidationError, load_experiment
from amr_optimizer.lab.runner import analyse, report, results_to_dict, run_experiment, write_results


def _commit() -> str | None:
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).parent,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return done.stdout.strip() or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="amr-lab", description=(__doc__ or "").splitlines()[0])
    parser.add_argument("experiment", type=Path, help="an experiment file (JSON)")
    parser.add_argument("--out", type=Path, help="write the rows and the analysis to this file")
    parser.add_argument("--workers", type=int, default=1, help="processes to run in (default 1)")
    parser.add_argument(
        "--model", choices=MODELS, help="use this disturbance model instead of the file's"
    )
    args = parser.parse_args(argv)
    try:
        experiment = load_experiment(args.experiment)
        if args.model:
            experiment = dataclasses.replace(
                experiment,
                disturbances=dataclasses.replace(experiment.disturbances, model=args.model),
            )
    except (OSError, ExperimentValidationError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    rows = run_experiment(experiment, workers=args.workers)
    contrasts = analyse(experiment, rows)
    print(report(experiment, rows, contrasts))
    if args.out:
        write_results(args.out, results_to_dict(experiment, rows, contrasts, commit=_commit()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
