"""Tests for the robot state machine."""

import dataclasses
import itertools
from collections.abc import Callable

import pytest

from amr_optimizer.domain import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    OrderId,
    RobotId,
    RobotPhase,
    RobotState,
    RobotStateError,
    TransportOrder,
)
from amr_optimizer.routing import Route
from amr_optimizer.warehouse import NodeId

IDLE, TO_PICKUP, LOADING = RobotPhase.IDLE, RobotPhase.TO_PICKUP, RobotPhase.LOADING
TO_DROPOFF, UNLOADING = RobotPhase.TO_DROPOFF, RobotPhase.UNLOADING
REPOSITIONING = RobotPhase.REPOSITIONING

R1 = RobotId("R1")
H, X, P, Y, D = (NodeId(name) for name in ("H", "X", "P", "Y", "D"))  # home, ..., pickup, ..., drop
ORDER = TransportOrder(OrderId("O1"), pickup=P, dropoff=D, release_tick=5)
TO_PICKUP_ROUTE = Route((H, X, P), 2.0)
TO_DROPOFF_ROUTE = Route((P, Y, D), 3.0)
TO_HOLDING_ROUTE = Route((D, Y, H), 3.0)


def _job_states() -> list[RobotState]:
    """One full job: idle at H, assigned at tick 5, delivered at D at tick 105."""
    idle = RobotState.idle(R1, H)
    to_pickup = idle.assign(ORDER, TO_PICKUP_ROUTE, tick=5, until_tick=25)
    loading = to_pickup.arrive(tick=25, until_tick=55)
    to_dropoff = loading.finish_loading(TO_DROPOFF_ROUTE, tick=55, until_tick=85)
    unloading = to_dropoff.arrive(tick=85, until_tick=105)
    done = unloading.finish_unloading(tick=105)
    return [idle, to_pickup, loading, to_dropoff, unloading, done]


def _traffic_tail() -> list[RobotState]:
    """With traffic, the robot leaves the station after unloading and becomes idle at H."""
    unloading = _job_states()[4]
    repositioning = unloading.reposition(TO_HOLDING_ROUTE, tick=105, until_tick=140)
    return [unloading, repositioning, repositioning.finish_repositioning(tick=140)]


STATE_IN = {state.phase: state for state in _job_states()[:-1] + _traffic_tail()[1:2]}


# --- the transition table ----------------------------------------------------------------


def test_transition_table_is_exactly_the_job_cycle() -> None:
    assert dict(ALLOWED_TRANSITIONS) == {
        IDLE: {TO_PICKUP, LOADING, REPOSITIONING},
        TO_PICKUP: {LOADING},
        LOADING: {TO_DROPOFF},
        TO_DROPOFF: {UNLOADING},
        UNLOADING: {IDLE, REPOSITIONING},
        REPOSITIONING: {IDLE},
    }


def test_transition_table_is_read_only() -> None:
    with pytest.raises(TypeError):
        ALLOWED_TRANSITIONS[IDLE] = frozenset()  # type: ignore[index]


def test_every_phase_is_reachable_from_idle_and_leads_back_to_idle() -> None:
    def reachable(start: RobotPhase) -> set[RobotPhase]:
        seen, frontier = {start}, [start]
        while frontier:
            for nxt in ALLOWED_TRANSITIONS[frontier.pop()]:
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
        return seen

    assert reachable(IDLE) == set(RobotPhase)
    assert all(IDLE in reachable(phase) for phase in RobotPhase)


def test_every_allowed_transition_is_exercised_by_the_methods() -> None:
    job = _job_states()
    at_pickup = RobotState.idle(R1, P)
    direct = at_pickup.assign(ORDER, Route((P,), 0.0), tick=5, until_tick=35)
    evicted = RobotState.idle(R1, H).reposition(Route((H, X), 1.0), tick=5, until_tick=15)
    exercised = {(a.phase, b.phase) for a, b in itertools.pairwise(job)}
    exercised |= {(a.phase, b.phase) for a, b in itertools.pairwise(_traffic_tail())}
    exercised |= {(at_pickup.phase, direct.phase), (IDLE, evicted.phase)}

    assert exercised == {(a, b) for a, targets in ALLOWED_TRANSITIONS.items() for b in targets}


# --- a full job ------------------------------------------------------------------------


def test_full_job_moves_through_every_phase_in_order() -> None:
    job = _job_states()
    idle, to_pickup, loading, to_dropoff, unloading, done = job

    assert [s.phase for s in job] == [IDLE, TO_PICKUP, LOADING, TO_DROPOFF, UNLOADING, IDLE]
    assert [s.node for s in job] == [H, H, P, P, D, D]
    assert [(s.since_tick, s.until_tick) for s in job] == [
        (0, None),
        (5, 25),
        (25, 55),
        (55, 85),
        (85, 105),
        (105, None),
    ]
    assert to_pickup.route == TO_PICKUP_ROUTE
    assert to_dropoff.route == TO_DROPOFF_ROUTE
    assert loading.route is None and unloading.route is None
    assert all(s.order == ORDER for s in (to_pickup, loading, to_dropoff, unloading))
    assert idle.order is None and done.order is None
    assert idle.is_idle and done.is_idle and not loading.is_idle


def test_robot_already_at_pickup_starts_loading_on_assignment() -> None:
    state = RobotState.idle(R1, P).assign(ORDER, Route((P,), 0.0), tick=5, until_tick=35)

    assert (state.phase, state.node, state.until_tick, state.route) == (LOADING, P, 35, None)


def test_loading_may_take_zero_ticks() -> None:
    loading = STATE_IN[TO_PICKUP].arrive(tick=25, until_tick=25)

    to_dropoff = loading.finish_loading(TO_DROPOFF_ROUTE, tick=25, until_tick=55)
    assert to_dropoff.phase is TO_DROPOFF


def test_travel_phases_are_flagged() -> None:
    travelling = {phase for phase in RobotPhase if phase.is_travelling}

    assert travelling == {TO_PICKUP, TO_DROPOFF, REPOSITIONING}


def test_repositioning_after_delivery_drops_the_order_and_ends_idle_elsewhere() -> None:
    unloading, repositioning, idle = _traffic_tail()

    assert (repositioning.phase, repositioning.node, repositioning.order) == (
        REPOSITIONING,
        D,
        None,
    )
    assert (repositioning.since_tick, repositioning.until_tick) == (105, 140)
    assert repositioning.route == TO_HOLDING_ROUTE
    assert (idle.phase, idle.node, idle.since_tick) == (IDLE, H, 140)
    assert unloading.order == ORDER


def test_an_idle_robot_can_be_moved_out_of_the_way() -> None:
    moving = RobotState.idle(R1, P, tick=3).reposition(Route((P, X), 1.0), tick=7, until_tick=17)

    assert (moving.phase, moving.node, moving.until_tick) == (REPOSITIONING, P, 17)
    assert moving.finish_repositioning(tick=17) == RobotState.idle(R1, X, tick=17)


def test_robot_state_is_immutable() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        STATE_IN[IDLE].node = P  # type: ignore[misc]


# --- refused transitions ------------------------------------------------------------------


def _now(state: RobotState) -> int:
    """The tick at which the state's next transition is due."""
    if state.until_tick is not None:
        return state.until_tick
    return max(state.since_tick, ORDER.release_tick)


ACTIONS: dict[str, Callable[[RobotState], RobotState]] = {
    "assign": lambda s: s.assign(ORDER, TO_PICKUP_ROUTE, tick=_now(s), until_tick=_now(s) + 20),
    "arrive": lambda s: s.arrive(tick=_now(s), until_tick=_now(s) + 30),
    "finish_loading": lambda s: s.finish_loading(
        TO_DROPOFF_ROUTE, tick=_now(s), until_tick=_now(s) + 30
    ),
    "finish_unloading": lambda s: s.finish_unloading(tick=_now(s)),
    "reposition": lambda s: s.reposition(
        Route((s.node, Y), 1.0), tick=_now(s), until_tick=_now(s) + 10
    ),
    "finish_repositioning": lambda s: s.finish_repositioning(tick=_now(s)),
}
LEGAL = {
    (IDLE, "assign"),
    (TO_PICKUP, "arrive"),
    (LOADING, "finish_loading"),
    (TO_DROPOFF, "arrive"),
    (UNLOADING, "finish_unloading"),
    (IDLE, "reposition"),
    (UNLOADING, "reposition"),
    (REPOSITIONING, "finish_repositioning"),
}


@pytest.mark.parametrize("phase", list(RobotPhase), ids=lambda p: p.name)
@pytest.mark.parametrize("action", sorted(ACTIONS))
def test_only_listed_transitions_are_accepted(phase: RobotPhase, action: str) -> None:
    state = STATE_IN[phase]
    if (phase, action) in LEGAL:
        assert ACTIONS[action](state).phase in ALLOWED_TRANSITIONS[phase]
    else:
        with pytest.raises(InvalidTransitionError, match=f"while {phase.name}"):
            ACTIONS[action](state)


def test_transition_at_the_wrong_tick_is_refused() -> None:
    with pytest.raises(RobotStateError, match="TO_PICKUP ends at tick 25, not 24"):
        STATE_IN[TO_PICKUP].arrive(tick=24, until_tick=50)
    with pytest.raises(RobotStateError, match="ends at tick 105, not 106"):
        STATE_IN[UNLOADING].finish_unloading(tick=106)


def test_order_cannot_be_assigned_before_its_release() -> None:
    with pytest.raises(RobotStateError, match="released at tick 5, cannot assign it at 4"):
        STATE_IN[IDLE].assign(ORDER, TO_PICKUP_ROUTE, tick=4, until_tick=24)


def test_assignment_cannot_go_back_in_time() -> None:
    idle_since_10 = RobotState.idle(R1, H, tick=10)
    with pytest.raises(RobotStateError, match="before the current phase began"):
        idle_since_10.assign(ORDER, TO_PICKUP_ROUTE, tick=9, until_tick=29)


@pytest.mark.parametrize(
    ("route", "message"),
    [
        (Route((X, P), 1.0), "route starts at 'X', robot is at 'H'"),
        (Route((H, X), 1.0), "TO_PICKUP route must end at 'P'"),
        (Route((H,), 0.0), "LOADING must happen at 'P'"),  # robot is not at the pickup
    ],
)
def test_assignment_route_must_lead_from_the_robot_to_the_pickup(
    route: Route, message: str
) -> None:
    with pytest.raises(RobotStateError, match=message):
        STATE_IN[IDLE].assign(ORDER, route, tick=5, until_tick=25)


def test_dropoff_route_must_lead_from_the_pickup_to_the_dropoff() -> None:
    with pytest.raises(RobotStateError, match="TO_DROPOFF route must end at 'D'"):
        STATE_IN[LOADING].finish_loading(Route((P, Y), 1.0), tick=55, until_tick=85)
    with pytest.raises(RobotStateError, match="route must start at 'P'"):
        STATE_IN[LOADING].finish_loading(Route((X, Y, D), 2.0), tick=55, until_tick=85)


def test_a_phase_cannot_end_before_it_begins() -> None:
    with pytest.raises(RobotStateError, match="cannot end before it begins"):
        STATE_IN[TO_PICKUP].arrive(tick=25, until_tick=24)


def test_travel_takes_at_least_one_tick() -> None:
    with pytest.raises(RobotStateError, match="at least one tick"):
        STATE_IN[IDLE].assign(ORDER, TO_PICKUP_ROUTE, tick=5, until_tick=5)


# --- states that cannot exist ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda: RobotState(RobotId(""), IDLE, H, 0), "non-empty"),
        (lambda: RobotState(R1, IDLE, H, -1), "since_tick must be non-negative"),
        (lambda: RobotState(R1, IDLE, H, 0, order=ORDER), "idle robot has no order"),
        (lambda: RobotState(R1, IDLE, H, 0, until_tick=5), "idle robot has no order"),
        (lambda: RobotState(R1, LOADING, P, 0, until_tick=5), "needs an order and an end"),
        (lambda: RobotState(R1, LOADING, P, 0, None, ORDER), "needs an order and an end"),
        (lambda: RobotState(R1, LOADING, H, 0, 5, ORDER), "LOADING must happen at 'P'"),
        (lambda: RobotState(R1, UNLOADING, P, 0, 5, ORDER), "UNLOADING must happen at 'D'"),
        (
            lambda: RobotState(R1, LOADING, P, 0, 5, ORDER, TO_DROPOFF_ROUTE),
            "only a travelling robot has a route",
        ),
        (lambda: RobotState(R1, TO_PICKUP, H, 0, 5, ORDER), "needs a route of at least one edge"),
        (
            lambda: RobotState(R1, TO_PICKUP, P, 0, 5, ORDER, Route((P,), 0.0)),
            "needs a route of at least one edge",
        ),
        (
            lambda: RobotState(R1, REPOSITIONING, D, 0, 5, ORDER, TO_HOLDING_ROUTE),
            "repositioning robot has no order",
        ),
        (lambda: RobotState(R1, REPOSITIONING, D, 0, None, None, TO_HOLDING_ROUTE), "end tick"),
        (lambda: RobotState(R1, REPOSITIONING, D, 0, 5), "route of at least one edge"),
        (
            lambda: RobotState(R1, REPOSITIONING, H, 0, 5, None, TO_HOLDING_ROUTE),
            "route must start at 'H'",
        ),
        (
            lambda: RobotState(R1, REPOSITIONING, D, 5, 5, None, TO_HOLDING_ROUTE),
            "at least one tick",
        ),
    ],
)
def test_inconsistent_states_cannot_be_constructed(
    make: Callable[[], RobotState], message: str
) -> None:
    with pytest.raises(RobotStateError, match=message):
        make()
