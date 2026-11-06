"""Tests for the HTTP service's metadata and planning endpoints (D29).

Most tests run the app's CPU-bound work in a thread pool, which is quicker to start; one test uses
the real process pool, to show that requests and results cross process boundaries.
"""

import random
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from types import MappingProxyType
from typing import Any

import pytest
from fastapi.testclient import TestClient

from amr_optimizer.api import create_app
from amr_optimizer.dispatch import POLICIES
from amr_optimizer.domain import RobotId, RobotState, TimeModel, TravelTimes
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import DispatchSnapshot, TravelPlanner
from amr_optimizer.warehouse import PRESETS, NodeId, generate_grid_warehouse

GRAPH = generate_grid_warehouse(PRESETS["large"])


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with ThreadPoolExecutor(max_workers=2) as pool, TestClient(create_app(executor=pool)) as c:
        yield c


# --- metadata -------------------------------------------------------------------------------------


def test_health_reports_the_version_and_the_tick(client: TestClient) -> None:
    body = client.get("/health").json()

    assert body["status"] == "ok" and body["tick_s"] == 0.1 and body["version"]


def test_maps_are_the_presets(client: TestClient) -> None:
    maps = {m["name"]: m for m in client.get("/maps").json()}

    assert set(maps) == set(PRESETS)
    assert maps["large"]["nodes"] == len(GRAPH) and maps["large"]["edges"] == GRAPH.num_edges


def test_a_map_lists_its_nodes_and_an_unknown_map_is_not_found(client: TestClient) -> None:
    nodes = client.get("/maps/large").json()["nodes"]

    assert len(nodes) == len(GRAPH)
    assert {n["kind"] for n in nodes} == {k.kind.value for k in GRAPH.nodes}
    assert client.get("/maps/nowhere").status_code == 404


def test_policies_are_the_registered_ones(client: TestClient) -> None:
    assert client.get("/policies").json() == sorted(POLICIES)


# --- planning -------------------------------------------------------------------------------------


def _case(seed: int) -> tuple[dict[str, Any], DispatchSnapshot]:
    """A planning request built from a generated scenario, and the same snapshot built directly."""
    rng = random.Random(seed)
    scenario = generate_scenario(GRAPH, ScenarioSpec(robots=8, orders_per_hour=300), seed=seed)
    orders = scenario.orders[: rng.randint(1, 12)]
    tick = max(o.release_tick for o in orders)
    robots, ready = [], {}
    for robot in scenario.robots:
        entry: dict[str, Any] = {"id": robot.robot_id, "node": robot.node}
        if rng.random() < 0.4:  # busy: free at its node a little later
            entry["ready_tick"] = tick + rng.randint(0, 600)
            ready[robot.robot_id] = (entry["ready_tick"], robot.node)
        robots.append(entry)
    request = {
        "map": "large",
        "tick": tick,
        "robots": robots,
        "orders": [
            {"id": o.id, "pickup": o.pickup, "dropoff": o.dropoff, "release_tick": o.release_tick}
            for o in orders
        ],
    }
    time_model = TimeModel(0.1)
    snapshot = DispatchSnapshot(
        tick,
        tuple(RobotState.idle(RobotId(r["id"]), NodeId(r["node"])) for r in robots),
        orders,
        TravelPlanner(TravelTimes(GRAPH, speed_mps=1.0, time_model=time_model)),
        load_ticks=100,
        unload_ticks=100,
        planned_ready=MappingProxyType(ready),
        unavailable=frozenset(ready),
    )
    return request, snapshot


@pytest.mark.parametrize("policy", sorted(POLICIES))
def test_the_endpoint_decides_as_the_policy_does(client: TestClient, policy: str) -> None:
    for seed in range(4):
        request, snapshot = _case(seed)

        response = client.post("/plan", json=request | {"policy": policy})

        assert response.status_code == 200, response.text
        expected = [
            {"robot": a.robot_id, "order": a.order_id} for a in POLICIES[policy]().assign(snapshot)
        ]
        assert response.json()["assignments"] == expected, f"seed {seed}"
        busy = {r["id"] for r in request["robots"] if "ready_tick" in r}
        assert busy.isdisjoint(a["robot"] for a in expected)  # only idle robots start a job


def test_a_look_ahead_policy_can_leave_an_order_for_a_busy_robot(client: TestClient) -> None:
    # One order, an idle robot far away and a busy robot at the pickup, free in 10 s. Greedy sends
    # the idle robot; insertion plans the order for the busy robot and starts nothing now.
    storage = sorted(n.id for n in GRAPH.nodes if n.kind.value == "storage")
    stations = sorted(n.id for n in GRAPH.nodes if n.kind.value == "station")
    pickup, far = storage[0], storage[-1]
    request = {
        "tick": 0,
        "robots": [{"id": "near", "node": pickup, "ready_tick": 100}, {"id": "far", "node": far}],
        "orders": [{"id": "O1", "pickup": pickup, "dropoff": stations[0], "release_tick": 0}],
    }

    greedy = client.post("/plan", json=request | {"policy": "greedy"}).json()
    insertion = client.post("/plan", json=request | {"policy": "insertion"}).json()

    assert greedy["assignments"] == [{"robot": "far", "order": "O1"}]
    assert insertion["assignments"] == []


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"policy": "random"}, "unknown policy"),
        ({"map": "nowhere"}, "unknown map"),
        ({"robots": []}, "at least 1"),
        ({"robots": [{"id": "R1", "node": "X01-01"}, {"id": "R1", "node": "X01-02"}]}, "unique"),
        ({"robots": [{"id": "R1", "node": "nowhere"}]}, "not on map"),
        ({"robots": [{"id": "R1", "node": "X01-01", "ready_tick": 3}], "tick": 5}, "before tick"),
        (
            {"orders": [{"id": "O1", "pickup": "a", "dropoff": "b", "release_tick": 9}]},
            "not released",
        ),
        ({"robots": [{"id": f"R{i}", "node": "X01-01"} for i in range(101)]}, "at most 100"),
        ({"surprise": 1}, "Extra inputs"),
    ],
)
def test_invalid_requests_are_refused_with_a_reason(
    client: TestClient, change: dict[str, Any], reason: str
) -> None:
    request = {"robots": [{"id": "R1", "node": "X01-01"}]} | change

    response = client.post("/plan", json=request)

    assert response.status_code == 422
    assert reason in response.text


def test_an_order_with_the_same_pickup_and_dropoff_is_refused(client: TestClient) -> None:
    request = {
        "robots": [{"id": "R1", "node": "X01-01"}],
        "orders": [{"id": "O1", "pickup": "X01-01", "dropoff": "X01-01", "release_tick": 0}],
    }

    response = client.post("/plan", json=request)

    assert response.status_code == 422
    assert "pickup and drop-off are both" in response.text


def test_planning_runs_in_worker_processes() -> None:
    request, snapshot = _case(0)

    with TestClient(create_app(workers=1)) as client:
        response = client.post("/plan", json=request | {"policy": "cpsat-fair"})

    expected = POLICIES["cpsat-fair"]().assign(snapshot)
    assert [(a["robot"], a["order"]) for a in response.json()["assignments"]] == [
        (a.robot_id, a.order_id) for a in expected
    ]
