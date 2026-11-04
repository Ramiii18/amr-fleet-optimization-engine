"""Discrete-event simulation: the event loop, the dispatch interface and the event log."""

from amr_optimizer.sim.log import ORDER_LIFECYCLE, EventKind, LogEntry
from amr_optimizer.sim.planner import TravelPlanner
from amr_optimizer.sim.policy import (
    Assignment,
    DispatchPolicy,
    DispatchSnapshot,
    InvalidAssignmentError,
)
from amr_optimizer.sim.simulator import SimulationInvariantError, SimulationResult, simulate

__all__ = [
    "ORDER_LIFECYCLE",
    "Assignment",
    "DispatchPolicy",
    "DispatchSnapshot",
    "EventKind",
    "InvalidAssignmentError",
    "LogEntry",
    "SimulationInvariantError",
    "SimulationResult",
    "TravelPlanner",
    "simulate",
]
