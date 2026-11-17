"""Read scenarios from JSON files: your own map, robots and orders (D32).

Format (version 1)::

    {
      "format": "amr-scenario",
      "version": 1,
      "description": "optional free text",
      "map": "small",
      "tick_s": 0.1, "speed_mps": 1.0, "load_s": 10.0, "unload_s": 10.0,
      "robots": [{"id": "R1", "node": "PK00"}, ...],
      "orders": [{"id": "O01", "pickup": "S00-01", "dropoff": "ST00", "release_s": 0}, ...]
    }

- ``map`` is a preset name, the path of a map or layout file (a relative path is relative to
  the scenario file), or a map or layout object (`amr_optimizer.warehouse.io`,
  `amr_optimizer.layout`).
- ``description``, ``tick_s``, ``speed_mps``, ``load_s`` and ``unload_s`` are optional. The
  defaults are those of generated scenarios (`ScenarioSpec`).
- Times are in seconds. They become ticks by rounding up (D12), so nothing happens earlier
  than the file says.
- Orders may be listed in any order. They are sorted by release time, keeping the file's
  order among orders released at the same tick.

Parsing is strict, as for maps: missing fields, unknown fields and wrong types raise a
`ScenarioFormatError` that says where the problem is (e.g. ``orders[3].release_s``). Then
the usual validation applies: the map's own, each order's, and the scenario's as a whole
(unknown nodes, duplicate ids, robots sharing a node).
"""

import json
import math
from collections.abc import Mapping
from pathlib import Path

from amr_optimizer.domain import (
    OrderId,
    OrderValidationError,
    RobotId,
    RobotState,
    RobotStateError,
    TimeModel,
    TransportOrder,
)
from amr_optimizer.layout import load_map_file, map_from_dict
from amr_optimizer.scenario.generator import ScenarioSpec
from amr_optimizer.scenario.model import Scenario, ScenarioValidationError
from amr_optimizer.warehouse import PRESETS, NodeId, WarehouseGraph, generate_grid_warehouse

FORMAT_NAME = "amr-scenario"
FORMAT_VERSION = 1

_REQUIRED = frozenset({"format", "version", "map", "robots", "orders"})
_OPTIONAL = frozenset({"description", "tick_s", "speed_mps", "load_s", "unload_s"})
_DEFAULTS = ScenarioSpec()


class ScenarioFormatError(ScenarioValidationError):
    """A scenario file is not valid JSON in the expected structure."""


def load_scenario(path: Path | str) -> Scenario:
    """Read a scenario file. A relative map path is resolved next to the file."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ScenarioFormatError(f"{path}: not valid JSON ({error})") from error
    return scenario_from_dict(data, base_dir=path.parent)


def scenario_from_dict(data: object, *, base_dir: Path | None = None) -> Scenario:
    """Build a scenario from parsed JSON.

    Raises `ScenarioFormatError` or `ScenarioValidationError`, or the map's
    `MapValidationError`. All of them are `ValueError`s.
    """
    top = _as_object(data, "scenario", _REQUIRED, _OPTIONAL)
    if top["format"] != FORMAT_NAME:
        raise ScenarioFormatError(
            f"scenario.format: must be {FORMAT_NAME!r}, got {top['format']!r}"
        )
    if top["version"] != FORMAT_VERSION or isinstance(top["version"], bool):
        raise ScenarioFormatError(f"scenario.version: unsupported version {top['version']!r}")
    if "description" in top and not isinstance(top["description"], str):
        raise ScenarioFormatError("scenario.description: expected a string")

    graph = _parse_map(top["map"], base_dir)
    time_model = TimeModel(_number(top, "tick_s", "scenario", _DEFAULTS.tick_s, positive=True))
    speed_mps = _number(top, "speed_mps", "scenario", _DEFAULTS.speed_mps, positive=True)
    load_s = _number(top, "load_s", "scenario", _DEFAULTS.load_s)
    unload_s = _number(top, "unload_s", "scenario", _DEFAULTS.unload_s)

    robots = tuple(
        _parse_robot(item, f"robots[{i}]") for i, item in enumerate(_as_list(top, "robots"))
    )
    orders = [
        _parse_order(item, f"orders[{i}]", time_model)
        for i, item in enumerate(_as_list(top, "orders"))
    ]
    orders.sort(key=lambda order: order.release_tick)  # stable: ties keep the file's order
    return Scenario(
        graph=graph,
        time_model=time_model,
        speed_mps=speed_mps,
        load_ticks=time_model.to_ticks(load_s),
        unload_ticks=time_model.to_ticks(unload_s),
        robots=robots,
        orders=tuple(orders),
    )


def _parse_map(value: object, base_dir: Path | None) -> WarehouseGraph:
    if isinstance(value, dict):
        return map_from_dict(value)
    if not isinstance(value, str):
        raise ScenarioFormatError(
            "scenario.map: expected a preset name, a map or layout file path, or a map object"
        )
    if value in PRESETS:
        return generate_grid_warehouse(PRESETS[value])
    path = Path(value)
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    if not path.is_file():
        raise ScenarioFormatError(
            f"scenario.map: {value!r} is neither a preset ({', '.join(PRESETS)}) nor a map file"
        )
    return load_map_file(path)


def _parse_robot(value: object, where: str) -> RobotState:
    obj = _as_object(value, where, frozenset({"id", "node"}))
    try:
        return RobotState.idle(
            RobotId(_string(obj, "id", where)), NodeId(_string(obj, "node", where))
        )
    except RobotStateError as error:
        raise ScenarioValidationError(f"{where}: {error}") from error


def _parse_order(value: object, where: str, time_model: TimeModel) -> TransportOrder:
    obj = _as_object(value, where, frozenset({"id", "pickup", "dropoff", "release_s"}))
    release_s = _number(obj, "release_s", where)
    try:
        return TransportOrder(
            id=OrderId(_string(obj, "id", where)),
            pickup=NodeId(_string(obj, "pickup", where)),
            dropoff=NodeId(_string(obj, "dropoff", where)),
            release_tick=time_model.to_ticks(release_s),
        )
    except OrderValidationError as error:
        raise ScenarioValidationError(f"{where}: {error}") from error


# --- strict field parsing -----------------------------------------------------------------


def _as_object(
    value: object,
    where: str,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ScenarioFormatError(f"{where}: expected an object")
    missing = required - value.keys()
    if missing:
        raise ScenarioFormatError(f"{where}: missing field(s) {sorted(missing)}")
    unknown = value.keys() - required - optional
    if unknown:
        raise ScenarioFormatError(f"{where}: unknown field(s) {sorted(unknown)}")
    return value


def _as_list(obj: Mapping[str, object], field: str) -> list[object]:
    value = obj[field]
    if not isinstance(value, list):
        raise ScenarioFormatError(f"scenario.{field}: expected a list")
    return value


def _string(obj: Mapping[str, object], field: str, where: str) -> str:
    value = obj[field]
    if not isinstance(value, str) or not value:
        raise ScenarioFormatError(f"{where}.{field}: expected a non-empty string, got {value!r}")
    return value


def _number(
    obj: Mapping[str, object],
    field: str,
    where: str,
    default: float | None = None,
    *,
    positive: bool = False,
) -> float:
    """A finite number that is non-negative (or positive), or ``default`` if it is absent."""
    if field not in obj and default is not None:
        return default
    value = obj[field]
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ScenarioFormatError(f"{where}.{field}: expected a number, got {value!r}")
    if value < 0 or (positive and value == 0):
        bound = "positive" if positive else "non-negative"
        raise ScenarioFormatError(f"{where}.{field}: must be {bound}, got {value!r}")
    return float(value)
