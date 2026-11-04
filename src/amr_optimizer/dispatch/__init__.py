"""Dispatch policies: which idle robot takes which pending order."""

from collections.abc import Callable, Mapping
from types import MappingProxyType

from amr_optimizer.dispatch.greedy import GreedyDispatch
from amr_optimizer.dispatch.hungarian import HungarianDispatch
from amr_optimizer.dispatch.matching import min_cost_matching
from amr_optimizer.sim import DispatchPolicy

POLICIES: Mapping[str, Callable[[], DispatchPolicy]] = MappingProxyType(
    {"greedy": GreedyDispatch, "hungarian": HungarianDispatch}
)
"""Every policy by name, as used by the command line and the comparison script."""

__all__ = ["POLICIES", "GreedyDispatch", "HungarianDispatch", "min_cost_matching"]
