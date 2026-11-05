"""Dispatch policies: which idle robot takes which pending order."""

from collections.abc import Callable, Mapping
from functools import partial
from types import MappingProxyType

from amr_optimizer.dispatch.cpsat import (
    CpSatDispatch,
    SolveOutcome,
    SolverSettings,
    SolveStatus,
    solve_plan,
)
from amr_optimizer.dispatch.greedy import GreedyDispatch
from amr_optimizer.dispatch.horizon import (
    DEFAULT_MAX_ORDERS,
    HorizonProblem,
    InvalidPlanError,
    Objective,
    Plan,
    PlanningProblemError,
    RobotAvailability,
    availability,
    build_problem,
    completion_offsets,
    completion_upper_bound,
    first_jobs,
    plan_cost,
    total_completion,
    worst_lead,
    worst_weight,
)
from amr_optimizer.dispatch.hungarian import HungarianDispatch
from amr_optimizer.dispatch.insertion import InsertionDispatch, insertion_plan, sequence_total
from amr_optimizer.dispatch.matching import min_cost_matching
from amr_optimizer.sim import DispatchPolicy

POLICIES: Mapping[str, Callable[[], DispatchPolicy]] = MappingProxyType(
    {
        "greedy": GreedyDispatch,
        "hungarian": HungarianDispatch,
        "insertion": InsertionDispatch,
        "cpsat": CpSatDispatch,
        "insertion-fair": partial(InsertionDispatch, objective=Objective.WORST_FIRST),
        "cpsat-fair": partial(CpSatDispatch, objective=Objective.WORST_FIRST),
    }
)
"""Every policy by name, as used by the command line and the comparison script."""

__all__ = [
    "DEFAULT_MAX_ORDERS",
    "POLICIES",
    "CpSatDispatch",
    "GreedyDispatch",
    "HorizonProblem",
    "HungarianDispatch",
    "InsertionDispatch",
    "InvalidPlanError",
    "Objective",
    "Plan",
    "PlanningProblemError",
    "RobotAvailability",
    "SolveOutcome",
    "SolveStatus",
    "SolverSettings",
    "availability",
    "build_problem",
    "completion_offsets",
    "completion_upper_bound",
    "first_jobs",
    "insertion_plan",
    "min_cost_matching",
    "plan_cost",
    "sequence_total",
    "solve_plan",
    "total_completion",
    "worst_lead",
    "worst_weight",
]
