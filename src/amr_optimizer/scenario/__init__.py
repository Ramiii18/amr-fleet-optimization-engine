"""Scenarios: the validated input of a simulation run, their seeded generation, and files."""

from amr_optimizer.scenario.generator import (
    ScenarioSpec,
    generate_orders,
    generate_scenario,
    place_robots,
)
from amr_optimizer.scenario.io import ScenarioFormatError, load_scenario, scenario_from_dict
from amr_optimizer.scenario.model import Scenario, ScenarioValidationError

__all__ = [
    "Scenario",
    "ScenarioFormatError",
    "ScenarioSpec",
    "ScenarioValidationError",
    "generate_orders",
    "generate_scenario",
    "load_scenario",
    "place_robots",
    "scenario_from_dict",
]
