"""The experiment lab: paired comparisons of dispatch policies over seeds (D36)."""

from amr_optimizer.lab.analysis import Contrast, bootstrap_interval, holm, paired_contrast
from amr_optimizer.lab.experiment import (
    Disturbances,
    Experiment,
    ExperimentValidationError,
    Family,
    experiment_from_dict,
    load_experiment,
)
from amr_optimizer.lab.runner import (
    UnpairedRunsError,
    analyse,
    report,
    results_to_dict,
    run_experiment,
    run_one,
    schedule_for,
    write_results,
)

__all__ = [
    "Contrast",
    "Disturbances",
    "Experiment",
    "ExperimentValidationError",
    "Family",
    "UnpairedRunsError",
    "analyse",
    "bootstrap_interval",
    "experiment_from_dict",
    "holm",
    "load_experiment",
    "paired_contrast",
    "report",
    "results_to_dict",
    "run_experiment",
    "run_one",
    "schedule_for",
    "write_results",
]
