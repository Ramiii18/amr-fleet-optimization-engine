"""Tests for the HTTP service's simulation jobs (D29)."""

import time
from collections.abc import Callable, Iterator
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import asdict
from typing import Any

import pytest
from fastapi.testclient import TestClient

from amr_optimizer.api import create_app
from amr_optimizer.dispatch import POLICIES
from amr_optimizer.domain import BatterySpec, ChargingPolicy
from amr_optimizer.metrics import compute_kpis
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import simulate
from amr_optimizer.traffic import DelayModel, Recovery
from amr_optimizer.warehouse import PRESETS, generate_grid_warehouse


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with ThreadPoolExecutor(max_workers=2) as pool, TestClient(create_app(executor=pool)) as c:
        yield c


def _finished(client: TestClient, job_id: str, timeout_s: float = 120.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while True:
        body: dict[str, Any] = client.get(f"/simulations/{job_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        assert time.monotonic() < deadline, f"job {job_id} did not finish"
        time.sleep(0.02)


# Everything at once: traffic, delays, batteries, breakdowns and replanning.
FULL = {
    "map": "small",
    "policy": "insertion-fair",
    "robots": 3,
    "orders_per_hour": 120,
    "duration_s": 900,
    "seed": 4,
    "traffic": True,
    "delays": {"share": 0.1, "min_s": 1, "max_s": 3},
    "charging": "opportunistic",
    "breakdowns": {"share": 0.02, "min_s": 10, "max_s": 60},
    "recovery": "replan",
}


def test_a_job_reports_the_kpis_of_the_same_run_made_directly(client: TestClient) -> None:
    submitted = client.post("/simulations", json=FULL)
    assert submitted.status_code == 202
    assert submitted.json()["status"] in ("queued", "running", "done")

    job = _finished(client, submitted.json()["id"])

    graph = generate_grid_warehouse(PRESETS["small"])
    spec = ScenarioSpec(robots=3, orders_per_hour=120, duration_s=900)
    result = simulate(
        generate_scenario(graph, spec, seed=4),
        POLICIES["insertion-fair"](),
        traffic=True,
        delays=DelayModel(0.1, 10, 30),
        delay_seed=4,
        battery=BatterySpec(),
        battery_seed=4,
        charging=ChargingPolicy.OPPORTUNISTIC,
        breakdowns=DelayModel(0.02, 100, 600),
        breakdown_seed=4,
        recovery=Recovery.REPLAN,
    )
    kpis = compute_kpis(result)
    assert job["status"] == "done", job
    assert job["result"]["kpis"] == asdict(kpis) | {"empty_share": kpis.empty_share}
    assert job["result"]["breakdowns"] == result.breakdowns
    assert job["result"]["replans"] == result.replans
    assert job["result"]["charging"]["charging_trips"] == result.charging_trips


def test_jobs_are_listed_and_an_unknown_one_is_not_found(client: TestClient) -> None:
    job_id = client.post("/simulations", json={"map": "small", "duration_s": 60}).json()["id"]
    _finished(client, job_id)

    assert job_id in [job["id"] for job in client.get("/simulations").json()]
    assert client.get("/simulations/nothing").status_code == 404


def test_undefined_kpis_are_null(client: TestClient) -> None:
    # One order per hour for a second: almost surely no order at all, so no completion time.
    request = {"map": "small", "orders_per_hour": 1, "duration_s": 1, "traffic": False}
    job = _finished(client, client.post("/simulations", json=request).json()["id"])

    assert job["status"] == "done"
    assert job["result"]["kpis"]["orders"] == 0
    assert job["result"]["kpis"]["completion_mean_s"] is None


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"traffic": False, "delays": {"share": 0.1, "min_s": 1, "max_s": 2}}, "need traffic"),
        ({"traffic": False, "charging": "when-low"}, "need traffic"),
        ({"recovery": "replan"}, "needs breakdowns"),
        ({"robots": 0}, "greater than or equal to 1"),
        ({"robots": 51}, "less than or equal to 50"),
        ({"duration_s": 10_000}, "less than or equal to 7200"),
        ({"delays": {"share": 0.1, "min_s": 3, "max_s": 2}}, "at least min_s"),
        ({"charging": "sometimes"}, "Input should be"),
        ({"policy": "random"}, "unknown policy"),
    ],
)
def test_invalid_simulation_requests_are_refused(
    client: TestClient, change: dict[str, Any], reason: str
) -> None:
    response = client.post("/simulations", json={"map": "small"} | change)

    assert response.status_code == 422
    assert reason in response.text


class _Scripted(Executor):
    """An executor whose futures do what the test says, and never run anything."""

    def __init__(self, settle: Callable[["Future[Any]"], None] | None = None) -> None:
        self._settle = settle

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> "Future[Any]":
        future: Future[Any] = Future()
        if self._settle is not None:
            self._settle(future)
        return future


def test_a_full_queue_refuses_new_jobs() -> None:
    with TestClient(create_app(executor=_Scripted(), max_pending_jobs=2)) as client:
        codes = [client.post("/simulations", json={"map": "small"}).status_code for _ in range(3)]
        listed = client.get("/simulations").json()

    assert codes == [202, 202, 429]
    assert [job["status"] for job in listed] == ["queued", "queued"]


def test_a_failing_job_reports_its_error() -> None:
    def fail(future: "Future[Any]") -> None:
        future.set_exception(RuntimeError("the engine failed"))

    with TestClient(create_app(executor=_Scripted(fail))) as client:
        job = client.post("/simulations", json={"map": "small"}).json()

    assert job["status"] == "failed"
    assert job["error"] == "RuntimeError: the engine failed"
    assert job["result"] is None


def test_simulations_run_in_worker_processes() -> None:
    with TestClient(create_app(workers=1)) as client:
        job_id = client.post("/simulations", json={"map": "small", "duration_s": 300}).json()["id"]
        job = _finished(client, job_id)

    assert job["status"] == "done" and job["result"]["kpis"]["orders"] > 0
