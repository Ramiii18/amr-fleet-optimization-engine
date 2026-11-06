"""Fixtures for the VDA 5050 tests: the official 3.0.0 schemas as an oracle (D30)."""

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

SCHEMAS = Path(__file__).resolve().parents[2] / "data" / "vda5050" / "3.0.0"
Check = Callable[[str, dict[str, Any]], None]


def _load(name: str) -> dict[str, Any]:
    text = (SCHEMAS / f"{name}.schema").read_text(encoding="utf-8")
    # The published order.schema has a trailing comma, which strict JSON rejects (see README).
    schema: dict[str, Any] = json.loads(re.sub(r",(\s*[}\]])", r"\1", text))
    return schema


def _unknown_keys(
    instance: Any, schema: dict[str, Any], root: dict[str, Any], at: str
) -> list[str]:
    """Keys in ``instance`` that the schema does not define: the schemas allow extra keys, but a
    field the specification does not know is a mistake in this adapter."""
    if "$ref" in schema:
        _, _, name = schema["$ref"].rpartition("/")
        schema = root["definitions"][name]
    found = []
    if isinstance(instance, dict) and "properties" in schema:
        for key, value in instance.items():
            if key not in schema["properties"]:
                found.append(f"{at}.{key}")
            else:
                found += _unknown_keys(value, schema["properties"][key], root, f"{at}.{key}")
    elif isinstance(instance, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(instance):
            found += _unknown_keys(item, schema["items"], root, f"{at}[{i}]")
    return found


@pytest.fixture(scope="session")
def check_message() -> Check:
    """``check_message(name, message)`` asserts that ``message`` is a valid ``name`` message of
    VDA 5050 3.0.0 and uses no field the specification does not define."""
    schemas = {name: _load(name) for name in ("order", "state", "instantActions", "connection")}
    validators = {name: Draft202012Validator(schema) for name, schema in schemas.items()}

    def check(name: str, message: dict[str, Any]) -> None:
        errors = [e.message for e in validators[name].iter_errors(message)]
        assert errors == [], errors
        assert _unknown_keys(message, schemas[name], schemas[name], name) == []

    return check
