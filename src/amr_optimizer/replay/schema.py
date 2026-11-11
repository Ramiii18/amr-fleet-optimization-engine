"""The replay trace format (D33) as a JSON Schema, draft 2020-12.

It describes the same structure as the strict parser in `trace.py`, for the API documentation
and for clients in other languages. The schema covers structure and types only. That the parts
fit together (a visit is on the map, a step drives an existing edge) is checked by the parser.

The tests validate real traces against this schema with ``jsonschema``, which shares no code
with the parser.
"""

from typing import Any

from amr_optimizer.replay.trace import CONFLICT_KINDS, FORMAT_NAME, SCHEMA_VERSION
from amr_optimizer.sim import EventKind
from amr_optimizer.warehouse import NodeKind

_TICK = {"type": "integer", "minimum": 0}
_ID = {"type": "string", "minLength": 1}
_SCALARS = {"type": "object", "additionalProperties": {"type": ["number", "string", "null"]}}


def _strict(properties: dict[str, Any]) -> dict[str, Any]:
    """An object with exactly these properties."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _array(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


TRACE_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "AMR replay trace",
    "description": "One finished run of the synthetic simulator: map, trajectories, orders, "
    "event log, KPIs and conflicts. Times are integer ticks of tick_s seconds.",
    **_strict(
        {
            "format": {"const": FORMAT_NAME},
            "schema_version": {"const": SCHEMA_VERSION},
            "source": {"type": "string"},
            "run": {"type": "object"},
            "policy": {"type": "string"},
            "traffic": {"type": "boolean"},
            "battery": {"type": "boolean"},
            "tick_s": {"type": "number", "exclusiveMinimum": 0},
            "end_tick": _TICK,
            "speed_mps": {"type": "number", "exclusiveMinimum": 0},
            "load_ticks": _TICK,
            "unload_ticks": _TICK,
            "map": _strict(
                {
                    "nodes": _array(
                        _strict(
                            {
                                "id": _ID,
                                "x": {"type": "number"},
                                "y": {"type": "number"},
                                "kind": {"enum": [kind.value for kind in NodeKind]},
                            }
                        )
                    ),
                    "edges": _array(
                        _strict(
                            {
                                "source": _ID,
                                "target": _ID,
                                "length_m": {"type": "number", "exclusiveMinimum": 0},
                            }
                        )
                    ),
                }
            ),
            "robots": {
                "type": "array",
                "minItems": 1,
                "items": _strict(
                    {
                        "id": _ID,
                        "visits": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "description": "[node, arrive tick, depart tick]; a null "
                                "departure is a hold",
                                "type": "array",
                                "prefixItems": [_ID, _TICK, {"type": ["integer", "null"]}],
                                "minItems": 3,
                                "maxItems": 3,
                            },
                        },
                    }
                ),
            },
            "orders": _array(
                _strict({"id": _ID, "pickup": _ID, "dropoff": _ID, "release_tick": _TICK})
            ),
            "events": _array(
                _strict(
                    {
                        "tick": _TICK,
                        "kind": {"enum": [kind.value for kind in EventKind]},
                        "order": _ID,
                        "node": _ID,
                        "robot": {"type": ["string", "null"]},
                        "distance_m": {"type": "number", "minimum": 0},
                    }
                )
            ),
            "outcome": _strict(
                {key: _array(_ID) for key in ("delivered", "in_progress", "pending", "unreleased")}
            ),
            "counters": _strict(
                {
                    key: _TICK
                    for key in (
                        "breakdowns",
                        "breakdown_ticks",
                        "replans",
                        "moves_out_of_the_way",
                        "deferred_assignments",
                    )
                }
            ),
            "kpis": _SCALARS,
            "charging": {"anyOf": [_SCALARS, {"type": "null"}]},
            "conflicts": _strict(
                {
                    "total": _TICK,
                    "listed": _array(
                        _strict(
                            {
                                "kind": {"enum": list(CONFLICT_KINDS)},
                                "resource": {**_array(_ID), "minItems": 1, "maxItems": 2},
                                "robots": {**_array(_ID), "minItems": 2, "maxItems": 2},
                                "start": _TICK,
                                "end": _TICK,
                            }
                        )
                    ),
                }
            ),
        }
    ),
}
