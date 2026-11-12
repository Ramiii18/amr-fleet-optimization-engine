"""Tests for replay traces through the HTTP service (D33)."""

import gzip
import json
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from typing import Any

import jsonschema
import pytest
from fastapi.testclient import TestClient

from amr_optimizer.api import create_app, work
from amr_optimizer.api.models import SimulationRequest
from amr_optimizer.api.traces import TraceStore, accepts_gzip
from amr_optimizer.dispatch import POLICIES
from amr_optimizer.domain import BatterySpec, ChargingPolicy
from amr_optimizer.replay import TRACE_JSON_SCHEMA, build_trace, trace_from_dict, trace_to_json
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import simulate
from amr_optimizer.traffic import DelayModel, Recovery
from amr_optimizer.warehouse import PRESETS, generate_grid_warehouse

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
SMALL = {"map": "small", "robots": 2, "duration_s": 300, "record_trace": True}


@pytest.fixture(scope="module")
def pool() -> Iterator[ThreadPoolExecutor]:
    with ThreadPoolExecutor(max_workers=2) as executor:
        yield executor


@pytest.fixture(scope="module")
def client(pool: ThreadPoolExecutor) -> Iterator[TestClient]:
    with TestClient(create_app(executor=pool)) as c:
        yield c


def _finished(client: TestClient, request: dict[str, Any]) -> dict[str, Any]:
    submitted = client.post("/simulations", json=request)
    assert submitted.status_code == 202, submitted.text
    deadline = time.monotonic() + 120
    while True:
        body: dict[str, Any] = client.get(f"/simulations/{submitted.json()['id']}").json()
        if body["status"] in ("done", "failed"):
            return body
        assert time.monotonic() < deadline, "the job did not finish"
        time.sleep(0.02)


def test_the_trace_of_a_job_is_the_run_made_directly(client: TestClient) -> None:
    job = _finished(client, FULL | {"record_trace": True})
    assert (job["status"], job["trace"]) == ("done", "available")

    response = client.get(f"/simulations/{job['id']}/trace")

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
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    trace = trace_from_dict(response.json())  # the strict parser accepts what the service sends
    jsonschema.Draft202012Validator(TRACE_JSON_SCHEMA).validate(response.json())
    assert trace.paths() == dict(result.trajectories)
    assert trace.events == result.log
    assert trace.orders == result.scenario.orders
    assert (trace.nodes, trace.edges) == (graph.nodes, graph.edges)
    assert trace.counters.breakdowns == result.breakdowns > 0
    # The trace and the job report the same KPIs, and the trace says how the run was made.
    assert trace.kpis == job["result"]["kpis"]
    assert trace.charging == job["result"]["charging"]
    assert trace.run == {
        key: job["request"][key] for key in job["request"] if key != "record_trace"
    }
    assert trace.run["seed"] == 4 and trace.run["delays"] == {"share": 0.1, "min_s": 1, "max_s": 3}
    # Byte for byte the text the core writes for the same run.
    assert response.text == trace_to_json(build_trace(result, run=trace.run))


def test_the_trace_is_sent_in_gzip_only_to_clients_that_accept_it(client: TestClient) -> None:
    job = _finished(client, SMALL)
    url = f"/simulations/{job['id']}/trace"

    packed = client.get(url, headers={"Accept-Encoding": "gzip"})
    plain = client.get(url, headers={"Accept-Encoding": "identity"})
    refused = client.get(url, headers={"Accept-Encoding": "br, gzip;q=0"})

    assert packed.headers["content-encoding"] == "gzip"
    assert "content-encoding" not in plain.headers and "content-encoding" not in refused.headers
    assert all(r.headers["vary"] == "Accept-Encoding" for r in (packed, plain, refused))
    assert packed.json() == plain.json() == refused.json()
    assert plain.content == refused.content == packed.text.encode("ascii")

    # The stored bytes themselves, as they go over the wire to a client that accepts gzip.
    with client.stream("GET", url, headers={"Accept-Encoding": "gzip"}) as raw:
        wire = b"".join(raw.iter_raw())
    assert int(raw.headers["content-length"]) == len(wire) < len(plain.content)
    assert gzip.decompress(wire) == plain.content


def test_the_same_request_gives_the_same_trace(client: TestClient) -> None:
    first, second = _finished(client, SMALL), _finished(client, SMALL)

    texts = [client.get(f"/simulations/{job['id']}/trace").text for job in (first, second)]

    assert first["id"] != second["id"] and texts[0] == texts[1]


def test_recording_a_trace_changes_nothing_else(client: TestClient) -> None:
    without = _finished(client, FULL)
    recorded = _finished(client, FULL | {"record_trace": True})

    def comparable(job: dict[str, Any]) -> dict[str, Any]:
        result = dict(job["result"])
        del result["compute_s"]  # wall time
        return result

    assert without["request"]["record_trace"] is False and without["trace"] == "none"
    assert comparable(without) == comparable(recorded)
    refused = client.get(f"/simulations/{without['id']}/trace")
    assert refused.status_code == 404 and "record_trace" in refused.json()["detail"]


def test_a_trace_needs_a_known_job(client: TestClient) -> None:
    response = client.get("/simulations/nothing/trace")

    assert response.status_code == 404 and "no simulation" in response.json()["detail"]


class _Scripted(Executor):
    """An executor whose futures do what the test says, and never run anything."""

    def __init__(self, settle: Callable[["Future[Any]"], None] | None = None) -> None:
        self._settle = settle

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> "Future[Any]":
        future: Future[Any] = Future()
        if self._settle is not None:
            self._settle(future)
        return future


def test_an_unfinished_job_has_a_pending_trace() -> None:
    with TestClient(create_app(executor=_Scripted())) as client:
        asked = client.post("/simulations", json=SMALL).json()
        not_asked = client.post("/simulations", json={"map": "small"}).json()
        response = client.get(f"/simulations/{asked['id']}/trace")

    assert (asked["trace"], not_asked["trace"]) == ("pending", "none")
    assert response.status_code == 409 and "not finished" in response.json()["detail"]


def test_a_failed_job_has_no_trace() -> None:
    def fail(future: "Future[Any]") -> None:
        future.set_exception(RuntimeError("the engine failed"))

    with TestClient(create_app(executor=_Scripted(fail))) as client:
        job = client.post("/simulations", json=SMALL).json()
        response = client.get(f"/simulations/{job['id']}/trace")

    assert (job["status"], job["trace"]) == ("failed", "none")
    assert response.status_code == 404 and "failed" in response.json()["detail"]


def test_only_the_most_recent_traces_are_kept(pool: ThreadPoolExecutor) -> None:
    with TestClient(create_app(executor=pool, max_traces=2)) as client:
        ids = [_finished(client, SMALL | {"seed": seed})["id"] for seed in (1, 2, 3)]
        states = [client.get(f"/simulations/{job_id}").json()["trace"] for job_id in ids]
        codes = [client.get(f"/simulations/{job_id}/trace").status_code for job_id in ids]
        gone = client.get(f"/simulations/{ids[0]}/trace").json()["detail"]
        jobs = client.get("/simulations").json()

    assert states == ["evicted", "available", "available"]
    assert codes == [410, 200, 200]
    assert "dropped to make room" in gone
    # The job and its KPIs outlive its trace.
    assert jobs[0]["status"] == "done" and jobs[0]["result"]["kpis"]["orders"] > 0


def test_traces_are_kept_within_a_total_size(pool: ThreadPoolExecutor) -> None:
    size = len(work.run_job(SimulationRequest.model_validate(SMALL)).trace_gzip or b"")
    assert size > 1000

    # Room for one trace and a half: the second pushes the first out.
    with TestClient(create_app(executor=pool, max_trace_bytes=size * 3 // 2)) as client:
        ids = [_finished(client, SMALL)["id"] for _ in range(2)]
        states = [client.get(f"/simulations/{job_id}").json()["trace"] for job_id in ids]
    assert states == ["evicted", "available"]

    # No room for even one.
    with TestClient(create_app(executor=pool, max_trace_bytes=size - 1)) as client:
        job = _finished(client, SMALL)
        response = client.get(f"/simulations/{job['id']}/trace")
    assert (job["status"], job["trace"]) == ("done", "too_large")
    assert response.status_code == 404 and "larger than the service keeps" in response.text


def test_traces_cross_the_process_boundary() -> None:
    with TestClient(create_app(workers=1)) as client:
        job = _finished(client, SMALL)
        response = client.get(f"/simulations/{job['id']}/trace")

    assert job["trace"] == "available"
    assert trace_from_dict(response.json()).kpis == job["result"]["kpis"]


def test_the_api_documentation_describes_the_trace(client: TestClient) -> None:
    document = client.get("/openapi.json")

    assert document.status_code == 200
    answers = document.json()["paths"]["/simulations/{job_id}/trace"]["get"]["responses"]
    assert set(answers) >= {"200", "404", "409", "410"}
    schema = answers["200"]["content"]["application/json"]["schema"]
    assert schema["properties"]["schema_version"] == {"const": 1}
    assert json.dumps(schema) == json.dumps(TRACE_JSON_SCHEMA)


def test_the_api_documentation_gives_the_form_its_limits(client: TestClient) -> None:
    # The browser viewer reads the limits of its run form from this document (web/src/lib/api.ts,
    # `limitsFromOpenApi`), instead of repeating them. If the shape below changes, the viewer's
    # form loses its limits and leaves all validation to the service: change both together.
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    request = schemas["SimulationRequest"]["properties"]
    disturbance = schemas["Disturbance"]["properties"]

    assert request["robots"] == {
        "type": "integer",
        "maximum": 50.0,
        "minimum": 1.0,
        "title": "Robots",
        "default": 5,
    }
    for name, maximum in (("orders_per_hour", 1000.0), ("duration_s", 7200.0)):
        assert request[name]["exclusiveMinimum"] == 0.0 and request[name]["maximum"] == maximum
        assert isinstance(request[name]["default"], float)
    assert (request["seed"]["minimum"], request["seed"]["maximum"]) == (0.0, 2**31 - 1)
    assert request["charging"]["anyOf"][0]["enum"] == [
        "when-low",
        "opportunistic",
        "opportunistic-early",
    ]
    assert request["recovery"]["enum"] == ["wait", "retime", "replan"]
    assert (request["map"]["default"], request["policy"]["default"]) == ("large", "greedy")
    assert request["traffic"]["default"] is True and request["recovery"]["default"] == "wait"
    assert (
        disturbance["share"]["exclusiveMinimum"] == 0.0 and disturbance["share"]["maximum"] == 1.0
    )
    assert disturbance["max_s"]["maximum"] == disturbance["min_s"]["maximum"] == 600.0


# --- the store -----------------------------------------------------------------------------------


def test_the_store_is_bounded_by_count_and_by_bytes() -> None:
    store = TraceStore(max_traces=3, max_bytes=10)

    assert store.put("a", b"1234") and store.put("b", b"123") and store.put("c", b"12")
    assert (len(store), store.total_bytes) == (3, 9)

    assert store.put("d", b"1")  # a fourth trace: the oldest goes, by count
    assert ("a" in store, len(store), store.total_bytes) == (False, 3, 6)

    assert store.put("e", b"12345678")  # 8 more bytes: "b" and "c" go, by size
    assert [key in store for key in "bcde"] == [False, False, True, True]
    assert (len(store), store.total_bytes) == (2, 9)

    assert not store.put("f", b"12345678901")  # larger than the whole store: refused
    assert ("f" in store, len(store), store.total_bytes) == (False, 2, 9)
    assert store.get("e") == b"12345678" and store.get("a") is None


def test_the_store_replaces_and_discards() -> None:
    store = TraceStore(max_traces=2, max_bytes=100)
    store.put("a", b"12345")
    store.put("b", b"1")

    store.put("a", b"12")  # again: counted once, and now the newest
    assert (len(store), store.total_bytes) == (2, 3)
    store.put("c", b"1")
    assert [key in store for key in "abc"] == [True, False, True]

    store.discard("a")
    store.discard("never stored")
    assert (len(store), store.total_bytes) == (1, 1)


def test_a_store_without_room_keeps_nothing() -> None:
    assert not TraceStore(max_traces=0).put("a", b"1")
    assert not TraceStore(max_bytes=0).put("a", b"1")
    with pytest.raises(ValueError, match="non-negative"):
        TraceStore(max_traces=-1)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, False),
        ("", False),
        ("gzip", True),
        ("GZIP", True),
        ("gzip, deflate, br", True),
        ("deflate, gzip;q=0.5", True),
        ("x-gzip", True),
        ("identity", False),
        ("br", False),
        ("gzip;q=0", False),
        ("gzip; q=0.0, *", False),
        ("*", True),
        ("*;q=0", False),
        ("br, *;q=0.1", True),
        ("gzip;q=oops", False),
    ],
)
def test_accept_encoding_is_read_as_the_standard_says(header: str | None, expected: bool) -> None:
    assert accepts_gzip(header) is expected
