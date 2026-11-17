"""The experiment lab: paired comparisons of dispatch policies, or of layouts, over seeds (D36,
D41)."""

from amr_optimizer.lab.analysis import Contrast, bootstrap_interval, holm, paired_contrast
from amr_optimizer.lab.experiment import (
    Battery,
    Disturbances,
    Experiment,
    ExperimentValidationError,
    Family,
    LayoutChoice,
    experiment_from_dict,
    load_experiment,
)
from amr_optimizer.lab.runner import (
    UnpairedRunsError,
    analyse,
    pairing_key,
    report,
    results_to_dict,
    run_experiment,
    run_one,
    schedule_for,
    write_results,
)

__all__ = [
    "Battery",
    "Contrast",
    "Disturbances",
    "Experiment",
    "ExperimentValidationError",
    "Family",
    "LayoutChoice",
    "UnpairedRunsError",
    "analyse",
    "bootstrap_interval",
    "experiment_from_dict",
    "holm",
    "load_experiment",
    "paired_contrast",
    "pairing_key",
    "report",
    "results_to_dict",
    "run_experiment",
    "run_one",
    "schedule_for",
    "write_results",
]
