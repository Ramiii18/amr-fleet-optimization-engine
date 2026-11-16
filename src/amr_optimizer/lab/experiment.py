"""Experiment files: what to run and how to judge it, fixed before the runs (D36).

    {
      "format": "amr-experiment", "version": 1,
      "name": "greedy against insertion-fair",
      "families": [{"name": "medium-8", "map": "medium", "robots": 8,
                    "orders_per_hour": 320.0, "duration_s": 1800.0}],
      "policies": ["greedy", "insertion-fair"],
      "baseline": "greedy",
      "seeds": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
      "disturbances": {"model": "schedule", "delay_share": 0.1, "delay_min_s": 1.0,
                       "delay_max_s": 4.0, "episode_min_s": 30.0, "episode_max_s": 120.0,
                       "breakdown_share": 0.01, "breakdown_gap_s": 600.0,
                       "repair_min_s": 20.0, "repair_max_s": 90.0, "recovery": "replan"},
      "primary_kpi": "completion_mean_s",
      "kpis": ["completion_p95_s", "completion_max_s"],
      "margin": 5.0, "confidence": 0.95, "resamples": 10000
    }

- A **family** is one map, fleet size and load. Seeds replicate a family, and a conclusion
  holds for that family only. Every family is analysed on its own.
- Each seed gives the scenario (orders, start positions) and the disturbances of one
  replication. Every policy runs every seed of every family.
- ``disturbances.model`` is ``none``, ``independent`` (a draw per corridor entry, D25 and D28:
  ``delay_share`` and ``breakdown_share`` are probabilities per entry) or ``schedule`` (fixed
  before the run, `traffic.disturbances`: ``delay_share`` is the share of time a corridor is
  slow, and ``breakdown_gap_s`` the mean time between a robot's breakdowns).
- The **primary KPI** and the **margin** are stated here, before any run. The primary tests
  are corrected for multiple comparisons; the other KPIs are exploratory.

Parsing is strict, like scenario and trace files: an unknown, missing or mistyped field is an
error that names its place.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path

from amr_optimizer.dispatch import POLICIES
from amr_optimizer.metrics import RunKpis
from amr_optimizer.traffic import Recovery
from amr_optimizer.warehouse import PRESETS

FORMAT_NAME = "amr-experiment"
FORMAT_VERSION = 1
MODELS = ("none", "independent", "schedule")
KPI_NAMES = tuple(f.name for f in fields(RunKpis) if f.type in (float, int, "float", "int"))


class ExperimentValidationError(ValueError):
    """An experiment file is malformed or inconsistent."""


@dataclass(frozen=True, slots=True)
class Family:
    name: str
    map: str
    robots: int
    orders_per_hour: float
    duration_s: float


@dataclass(frozen=True, slots=True)
class Disturbances:
    model: str = "none"
    delay_share: float = 0.0
    delay_min_s: float = 1.0
    delay_max_s: float = 1.0
    episode_min_s: float = 30.0
    episode_max_s: float = 120.0
    breakdown_share: float = 0.0
    breakdown_gap_s: float = 0.0
    repair_min_s: float = 1.0
    repair_max_s: float = 1.0
    recovery: str = "wait"


@dataclass(frozen=True, slots=True)
class Experiment:
    name: str
    families: tuple[Family, ...]
    policies: tuple[str, ...]
    baseline: str
    seeds: tuple[int, ...]
    disturbances: Disturbances
    primary_kpi: str = "completion_mean_s"
    kpis: tuple[str, ...] = ()
    margin: float = 0.0
    confidence: float = 0.95
    resamples: int = 10_000

    def __post_init__(self) -> None:
        def fail(message: str) -> ExperimentValidationError:
            return ExperimentValidationError(f"experiment: {message}")

        if not self.families or not self.seeds:
            raise fail("needs at least one family and one seed")
        if len({f.name for f in self.families}) != len(self.families):
            raise fail("family names must differ")
        if len(set(self.seeds)) != len(self.seeds) or any(seed < 0 for seed in self.seeds):
            raise fail("seeds must be distinct and non-negative")
        if len(self.policies) < 2 or len(set(self.policies)) != len(self.policies):
            raise fail("needs at least two distinct policies")
        for policy in self.policies:
            if policy not in POLICIES:
                raise fail(f"unknown policy {policy!r}; known: {sorted(POLICIES)}")
        if self.baseline not in self.policies:
            raise fail(f"the baseline {self.baseline!r} is not among the policies")
        for kpi in (self.primary_kpi, *self.kpis):
            if kpi not in KPI_NAMES:
                raise fail(f"unknown KPI {kpi!r}; known: {list(KPI_NAMES)}")
        if self.primary_kpi in self.kpis or len(set(self.kpis)) != len(self.kpis):
            raise fail("each KPI is listed once, and the primary KPI not among the others")
        for family in self.families:
            if family.map not in PRESETS:
                raise fail(f"family {family.name!r}: unknown map {family.map!r}")
            if family.robots < 1 or family.orders_per_hour <= 0 or family.duration_s <= 0:
                raise fail(f"family {family.name!r}: robots, load and duration must be positive")
        d = self.disturbances
        if d.model not in MODELS:
            raise fail(f"disturbances.model must be one of {list(MODELS)}, got {d.model!r}")
        if d.recovery not in {r.value for r in Recovery}:
            raise fail(f"disturbances.recovery: unknown recovery {d.recovery!r}")
        if not (0 <= d.delay_share < 1 and 0 <= d.breakdown_share < 1 and d.breakdown_gap_s >= 0):
            raise fail("disturbances: shares must be in [0, 1) and the gap non-negative")
        for name in ("delay", "episode", "repair"):
            low, high = getattr(d, f"{name}_min_s"), getattr(d, f"{name}_max_s")
            if not 0 < low <= high:
                raise fail(f"disturbances: needs 0 < {name}_min_s <= {name}_max_s")
        if self.margin < 0 or not 0 < self.confidence < 1 or self.resamples < 100:
            raise fail("needs margin >= 0, 0 < confidence < 1 and at least 100 resamples")

    def to_dict(self) -> dict[str, object]:
        def plain(value: object) -> dict[str, object]:
            return {name: getattr(value, name) for name in value.__slots__}  # type: ignore[attr-defined]

        return {
            "format": FORMAT_NAME,
            "version": FORMAT_VERSION,
            **plain(self),
            "families": [plain(family) for family in self.families],
            "policies": list(self.policies),
            "seeds": list(self.seeds),
            "kpis": list(self.kpis),
            "disturbances": plain(self.disturbances),
        }


def _object(
    value: object, where: str, required: tuple[str, ...], optional: tuple[str, ...] = ()
) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ExperimentValidationError(f"{where}: expected an object")
    unknown = sorted(set(value) - set(required) - set(optional))
    missing = sorted(set(required) - set(value))
    if unknown or missing:
        problem = f"unknown field(s) {unknown}" if unknown else f"missing field(s) {missing}"
        raise ExperimentValidationError(f"{where}: {problem}")
    return value


def _typed[T](value: object, kind: type[T], where: str) -> T:
    number = kind is float and isinstance(value, int)
    if isinstance(value, bool) or not (isinstance(value, kind) or number):
        raise ExperimentValidationError(f"{where}: expected {kind.__name__}, got {value!r}")
    return kind(value)  # type: ignore[call-arg]


def _items[T](value: object, kind: type[T], where: str) -> tuple[T, ...]:
    if not isinstance(value, list):
        raise ExperimentValidationError(f"{where}: expected a list")
    return tuple(_typed(item, kind, f"{where}[{i}]") for i, item in enumerate(value))


def experiment_from_dict(data: object) -> Experiment:
    """Build an experiment from parsed JSON, or raise `ExperimentValidationError` naming the
    place of the first problem."""
    optional = ("kpis", "margin", "confidence", "resamples", "primary_kpi")
    required = (
        "format",
        "version",
        "name",
        "families",
        "policies",
        "baseline",
        "seeds",
        "disturbances",
    )
    top = _object(data, "experiment", required, optional)
    if top["format"] != FORMAT_NAME or top["version"] != FORMAT_VERSION:
        raise ExperimentValidationError(
            f"experiment: expected format {FORMAT_NAME!r} version {FORMAT_VERSION}"
        )
    families = []
    if not isinstance(top["families"], list):
        raise ExperimentValidationError("experiment.families: expected a list")
    for i, item in enumerate(top["families"]):
        where = f"families[{i}]"
        obj = _object(item, where, ("name", "map", "robots", "orders_per_hour", "duration_s"))
        families.append(
            Family(
                name=_typed(obj["name"], str, f"{where}.name"),
                map=_typed(obj["map"], str, f"{where}.map"),
                robots=_typed(obj["robots"], int, f"{where}.robots"),
                orders_per_hour=_typed(obj["orders_per_hour"], float, f"{where}.orders_per_hour"),
                duration_s=_typed(obj["duration_s"], float, f"{where}.duration_s"),
            )
        )
    names = tuple(f.name for f in fields(Disturbances))
    obj = _object(top["disturbances"], "disturbances", ("model",), names[1:])
    numbers = {
        name: _typed(obj[name], float, f"disturbances.{name}")
        for name in names
        if name in obj and name not in ("model", "recovery")
    }
    disturbances = Disturbances(
        model=_typed(obj["model"], str, "disturbances.model"),
        recovery=_typed(obj.get("recovery", "wait"), str, "disturbances.recovery"),
        **numbers,
    )
    extra: dict[str, object] = {}
    for name, kind in (
        ("margin", float),
        ("confidence", float),
        ("resamples", int),
        ("primary_kpi", str),
    ):
        if name in top:
            extra[name] = _typed(top[name], kind, f"experiment.{name}")
    return Experiment(
        name=_typed(top["name"], str, "experiment.name"),
        families=tuple(families),
        policies=_items(top["policies"], str, "experiment.policies"),
        baseline=_typed(top["baseline"], str, "experiment.baseline"),
        seeds=_items(top["seeds"], int, "experiment.seeds"),
        disturbances=disturbances,
        kpis=_items(top.get("kpis", []), str, "experiment.kpis"),
        **extra,  # type: ignore[arg-type]
    )


def load_experiment(path: str | Path) -> Experiment:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ExperimentValidationError(f"{path}: not valid JSON: {error}") from error
    return experiment_from_dict(data)
