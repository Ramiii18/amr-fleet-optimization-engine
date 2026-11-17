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
- A family's ``map`` is a preset name, or the path of a map file or a layout file (D38). A
  relative path is relative to the experiment file, as in scenario files (D32).
- Each seed gives the scenario (orders, start positions) and the disturbances of one
  replication. Every policy runs every seed of every family.
- ``disturbances.model`` is ``none``, ``independent`` (a draw per corridor entry, D25 and D28:
  ``delay_share`` and ``breakdown_share`` are probabilities per entry) or ``schedule`` (fixed
  before the run, `traffic.disturbances`: ``delay_share`` is the share of time a corridor is
  slow, and ``breakdown_gap_s`` the mean time between a robot's breakdowns).
- The **primary KPI** and the **margin** are stated here, before any run. The primary tests
  are corrected for multiple comparisons; the other KPIs are exploratory.

**Version 2** (D41) adds two optional parts; a file without them is written as version 1.

- ``layouts``: ``[{"name": "baseline", "map": "hall.layout.json"}, ...]``. The experiment then
  compares **layouts**, not policies: families give the fleet and load but no map, ``baseline``
  names a layout, and each policy (one or more) is compared across layouts, paired by seed.
- ``battery``: ``{"charging": "opportunistic", "capacity_m": 3600.0, ...}`` (the fields of
  `BatterySpec`, all optional). Robots run on batteries (D26, D27), the map must have a charger,
  and the charging KPIs may be named as KPIs.

Parsing is strict, like scenario and trace files: an unknown, missing or mistyped field is an
error that names its place.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path

from amr_optimizer.dispatch import POLICIES
from amr_optimizer.domain import BatterySpec, ChargingPolicy
from amr_optimizer.metrics import ChargingKpis, RunKpis
from amr_optimizer.traffic import Recovery
from amr_optimizer.warehouse import PRESETS

FORMAT_NAME = "amr-experiment"
FORMAT_VERSION = 2
"""The newest version; version 1 files are read too, and written back as version 1."""
MODELS = ("none", "independent", "schedule")
KPI_NAMES = tuple(f.name for f in fields(RunKpis) if f.type in (float, int, "float", "int"))
CHARGING_KPI_NAMES = tuple(
    f.name for f in fields(ChargingKpis) if f.type in (float, int, "float", "int")
)


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
class LayoutChoice:
    """One of the layouts an experiment compares: a name and a map or layout file."""

    name: str
    map: str


_DEFAULT_BATTERY = BatterySpec()


@dataclass(frozen=True, slots=True)
class Battery:
    """The battery of every robot, and when robots charge (D26, D27)."""

    charging: str = ChargingPolicy.OPPORTUNISTIC.value
    capacity_m: float = _DEFAULT_BATTERY.capacity_m
    full_charge_s: float = _DEFAULT_BATTERY.full_charge_s
    low_share: float = _DEFAULT_BATTERY.low_share
    high_share: float = _DEFAULT_BATTERY.high_share
    start_min_share: float = _DEFAULT_BATTERY.start_min_share
    start_max_share: float = _DEFAULT_BATTERY.start_max_share

    def spec(self) -> BatterySpec:
        return BatterySpec(
            self.capacity_m,
            self.full_charge_s,
            self.low_share,
            self.high_share,
            self.start_min_share,
            self.start_max_share,
        )


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
    layouts: tuple[LayoutChoice, ...] = ()
    battery: Battery | None = None
    base_dir: Path | None = field(default=None, compare=False)
    """Where relative map paths start: the experiment file's folder. Not part of the file."""

    @property
    def compares_layouts(self) -> bool:
        return bool(self.layouts)

    @property
    def version(self) -> int:
        return 2 if self.layouts or self.battery is not None else 1

    def map_source(self, family: Family) -> str | Path:
        """A preset name, or the path of the family's map or layout file."""
        return self._source(family.map)

    def layout_source(self, layout: LayoutChoice) -> str | Path:
        return self._source(layout.map)

    def _source(self, name: str) -> str | Path:
        if name in PRESETS:
            return name
        path = Path(name)
        return path if path.is_absolute() or self.base_dir is None else self.base_dir / path

    def __post_init__(self) -> None:
        def fail(message: str) -> ExperimentValidationError:
            return ExperimentValidationError(f"experiment: {message}")

        if not self.families or not self.seeds:
            raise fail("needs at least one family and one seed")
        if len({f.name for f in self.families}) != len(self.families):
            raise fail("family names must differ")
        if len(set(self.seeds)) != len(self.seeds) or any(seed < 0 for seed in self.seeds):
            raise fail("seeds must be distinct and non-negative")
        least = 1 if self.compares_layouts else 2
        if len(self.policies) < least or len(set(self.policies)) != len(self.policies):
            raise fail(f"needs at least {'one' if least == 1 else 'two'} distinct policies")
        for policy in self.policies:
            if policy not in POLICIES:
                raise fail(f"unknown policy {policy!r}; known: {sorted(POLICIES)}")
        if self.compares_layouts:
            names = [layout.name for layout in self.layouts]
            if len(names) < 2 or len(set(names)) != len(names):
                raise fail("needs at least two layouts with distinct names")
            if self.baseline not in names:
                raise fail(f"the baseline {self.baseline!r} is not among the layouts")
            for layout in self.layouts:
                self._check_map(f"layout {layout.name!r}", layout.map)
        elif self.baseline not in self.policies:
            raise fail(f"the baseline {self.baseline!r} is not among the policies")
        known = KPI_NAMES + (CHARGING_KPI_NAMES if self.battery is not None else ())
        for kpi in (self.primary_kpi, *self.kpis):
            if kpi not in known:
                raise fail(f"unknown KPI {kpi!r}; known: {list(known)}")
        if self.primary_kpi in self.kpis or len(set(self.kpis)) != len(self.kpis):
            raise fail("each KPI is listed once, and the primary KPI not among the others")
        if self.battery is not None:
            if self.battery.charging not in {p.value for p in ChargingPolicy}:
                raise fail(f"battery.charging: unknown charging policy {self.battery.charging!r}")
            try:
                self.battery.spec()
            except ValueError as error:
                raise fail(f"battery: {error}") from None
        for family in self.families:
            if self.compares_layouts:
                if family.map:
                    raise fail(f"family {family.name!r}: the layouts give the map, not the family")
            else:
                self._check_map(f"family {family.name!r}", family.map)
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

    def _check_map(self, owner: str, name: str) -> None:
        source = self._source(name)
        if isinstance(source, Path) and not source.is_file():
            raise ExperimentValidationError(
                f"experiment: {owner}: unknown map {name!r}: neither a preset "
                f"({', '.join(PRESETS)}) nor a map or layout file"
            )

    def to_dict(self) -> dict[str, object]:
        def plain(value: object) -> dict[str, object]:
            return {name: getattr(value, name) for name in value.__slots__}  # type: ignore[attr-defined]

        own = plain(self)
        for name in ("base_dir", "layouts", "battery"):
            del own[name]
        families = [plain(family) for family in self.families]
        if self.compares_layouts:
            for family in families:
                del family["map"]
        data: dict[str, object] = {
            "format": FORMAT_NAME,
            "version": self.version,
            **own,
            "families": families,
            "policies": list(self.policies),
            "seeds": list(self.seeds),
            "kpis": list(self.kpis),
            "disturbances": plain(self.disturbances),
        }
        if self.layouts:
            data["layouts"] = [plain(layout) for layout in self.layouts]
        if self.battery is not None:
            data["battery"] = plain(self.battery)
        return data


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


def experiment_from_dict(data: object, *, base_dir: Path | None = None) -> Experiment:
    """Build an experiment from parsed JSON, or raise `ExperimentValidationError` naming the
    place of the first problem. Relative map paths are resolved against ``base_dir``."""
    optional: tuple[str, ...] = ("kpis", "margin", "confidence", "resamples", "primary_kpi")
    if isinstance(data, dict) and data.get("version") == 2:
        optional += ("layouts", "battery")
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
    version = top["version"]
    if top["format"] != FORMAT_NAME or isinstance(version, bool) or version not in (1, 2):
        raise ExperimentValidationError(
            f"experiment: expected format {FORMAT_NAME!r} version 1 or 2"
        )
    layouts = []
    if "layouts" in top:
        if not isinstance(top["layouts"], list):
            raise ExperimentValidationError("experiment.layouts: expected a list")
        for i, item in enumerate(top["layouts"]):
            obj = _object(item, f"layouts[{i}]", ("name", "map"))
            layouts.append(
                LayoutChoice(
                    _typed(obj["name"], str, f"layouts[{i}].name"),
                    _typed(obj["map"], str, f"layouts[{i}].map"),
                )
            )
    battery = None
    if "battery" in top:
        names = tuple(f.name for f in fields(Battery))
        obj = _object(top["battery"], "battery", (), names)
        battery = Battery(
            **{
                name: _typed(obj[name], str if name == "charging" else float, f"battery.{name}")
                for name in names
                if name in obj
            }  # type: ignore[arg-type]
        )
    family_fields: tuple[str, ...] = ("name", "robots", "orders_per_hour", "duration_s")
    if not layouts:
        family_fields = ("name", "map", *family_fields[1:])
    families = []
    if not isinstance(top["families"], list):
        raise ExperimentValidationError("experiment.families: expected a list")
    for i, item in enumerate(top["families"]):
        where = f"families[{i}]"
        obj = _object(item, where, family_fields)
        families.append(
            Family(
                name=_typed(obj["name"], str, f"{where}.name"),
                map=_typed(obj["map"], str, f"{where}.map") if "map" in obj else "",
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
        layouts=tuple(layouts),
        battery=battery,
        base_dir=base_dir,
        **extra,  # type: ignore[arg-type]
    )


def load_experiment(path: str | Path) -> Experiment:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ExperimentValidationError(f"{path}: not valid JSON: {error}") from error
    return experiment_from_dict(data, base_dir=Path(path).parent)
