"""The HTTP service (D29): the engine's planning and simulation behind FastAPI.

Every CPU-bound call runs in a process pool and the async endpoints await it, so the event loop
only ever parses, validates and answers. The pool is created when the app starts and shut down
when it stops. The interactive API documentation is served at ``/docs``.
"""

import asyncio
import gzip
import multiprocessing
import uuid
from collections.abc import AsyncIterator, Callable
from concurrent.futures import Executor, Future, ProcessPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from typing import TypeVar

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool

from amr_optimizer.api import work
from amr_optimizer.api.models import (
    TICK_S,
    EdgeOut,
    Health,
    Job,
    JobStatus,
    MapDetail,
    MapSummary,
    NodeOut,
    PlanRequest,
    PlanResponse,
    SimulationOutcome,
    SimulationRequest,
    TraceStatus,
)
from amr_optimizer.api.traces import MAX_KEPT_TRACES, MAX_TRACE_BYTES, TraceStore, accepts_gzip
from amr_optimizer.dispatch import POLICIES
from amr_optimizer.replay import TRACE_JSON_SCHEMA
from amr_optimizer.warehouse import PRESETS

T = TypeVar("T")
R = TypeVar("R")

MAX_PENDING_JOBS = 8  # queued or running; a new job beyond that is refused (429)
MAX_PENDING_PLANS = 32  # planning requests in progress; one more is refused (429)
MAX_KEPT_JOBS = 100  # finished jobs kept for polling; the oldest are forgotten first


@dataclass(slots=True)
class _JobEntry:
    """A job and, once it has finished, what it produced.

    A finished job is *settled*: its outcome is moved here, its trace into the store, and the
    future is dropped. A future keeps its whole result, so a kept future would hold the trace
    outside the store's limits.
    """

    id: str
    request: SimulationRequest
    submitted_at: datetime
    future: "Future[work.JobOutput] | None"
    outcome: SimulationOutcome | None = None
    error: str | None = None
    recorded: bool = False  # the job produced a trace
    stored: bool = False  # the store accepted it

    @property
    def pending(self) -> bool:
        return self.future is not None and not self.future.done()

    def settle(self, traces: TraceStore) -> None:
        future = self.future
        if future is None or not future.done():
            return
        self.future = None
        failure = None if future.cancelled() else future.exception()
        if future.cancelled() or failure is not None:
            self.error = "cancelled" if failure is None else f"{type(failure).__name__}: {failure}"
            return
        output = future.result()
        self.outcome = output.outcome
        if output.trace_gzip is not None:
            self.recorded = True
            self.stored = traces.put(self.id, output.trace_gzip)

    def trace_status(self, traces: TraceStore) -> TraceStatus:
        if self.future is not None:
            return "pending" if self.request.record_trace else "none"
        if not self.recorded:
            return "none"
        if not self.stored:
            return "too_large"
        return "available" if self.id in traces else "evicted"

    def view(self, traces: TraceStore) -> Job:
        self.settle(traces)
        status: JobStatus
        if self.future is not None:
            status = "running" if self.future.running() else "queued"
        else:
            status = "done" if self.error is None else "failed"
        return Job(
            id=self.id,
            status=status,
            submitted_at=self.submitted_at,
            request=self.request,
            result=self.outcome,
            error=self.error,
            trace=self.trace_status(traces),
        )


def create_app(
    *,
    executor: Executor | None = None,
    workers: int = 2,
    max_pending_jobs: int = MAX_PENDING_JOBS,
    max_pending_plans: int = MAX_PENDING_PLANS,
    max_traces: int = MAX_KEPT_TRACES,
    max_trace_bytes: int = MAX_TRACE_BYTES,
) -> FastAPI:
    """The service. Without ``executor``, it runs CPU-bound work in its own pool of ``workers``
    processes; tests may pass another executor, which the app then does not shut down. At most
    ``max_pending_jobs`` simulation jobs are queued or running at a time. Replay traces are kept
    for the ``max_traces`` most recent jobs that recorded one, and within ``max_trace_bytes``
    of compressed data in total (D33)."""
    jobs: dict[str, _JobEntry] = {}
    traces = TraceStore(max_traces, max_trace_bytes)
    plans_in_progress = [0]  # planning requests waiting for or running in the pool

    def settle_jobs() -> None:
        """Move what finished jobs produced out of their futures. Every job endpoint does this
        first, so at most ``max_pending_jobs`` results wait in futures, outside the store."""
        for entry in jobs.values():
            entry.settle(traces)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Spawned, not forked, workers: forking a process that already runs threads (the server's)
        # is unsafe, and spawning behaves the same on Linux as on Windows.
        spawn = multiprocessing.get_context("spawn")
        pool = executor or ProcessPoolExecutor(max_workers=workers, mp_context=spawn)
        app.state.executor = pool
        try:
            yield
        finally:
            if executor is None:
                pool.shutdown(cancel_futures=True)

    app = FastAPI(
        title="AMR Fleet Optimization Engine",
        version=version("amr-fleet-optimization-engine"),
        summary="Synthetic AMR fleet dispatch and simulation, as a local demonstration service.",
        lifespan=lifespan,
    )

    async def in_worker(request: Request, function: Callable[[T], R], argument: T) -> R:
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(request.app.state.executor, function, argument)
        except work.InvalidRequestError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/health")
    async def health() -> Health:
        return Health(status="ok", version=app.version, tick_s=TICK_S)

    @app.get("/maps")
    async def maps() -> list[MapSummary]:
        graphs = {name: work.preset_graph(name) for name in PRESETS}
        return [
            MapSummary(name=name, nodes=len(graph), edges=graph.num_edges)
            for name, graph in graphs.items()
        ]

    @app.get("/maps/{name}")
    async def map_detail(name: str) -> MapDetail:
        if name not in PRESETS:
            raise HTTPException(status_code=404, detail=f"unknown map {name!r}")
        graph = work.preset_graph(name)
        nodes = [NodeOut(id=n.id, x=n.x, y=n.y, kind=n.kind.value) for n in graph.nodes]
        edges = [
            EdgeOut(source=e.source, target=e.target, length_m=e.length_m) for e in graph.edges
        ]
        return MapDetail(name=name, nodes=nodes, edges=edges)

    @app.get("/policies")
    async def policies() -> list[str]:
        return sorted(POLICIES)

    @app.post("/plan")
    async def plan(body: PlanRequest, request: Request) -> PlanResponse:
        """One dispatch decision: which idle robot starts which pending order now.

        Planning shares the worker pool with simulation jobs, so it has its own admission limit:
        beyond ``max_pending_plans`` requests in progress, a new one is refused with 429."""
        if plans_in_progress[0] >= max_pending_plans:
            raise HTTPException(status_code=429, detail="too many planning requests in progress")
        plans_in_progress[0] += 1
        try:
            return await in_worker(request, work.decide, body)
        finally:
            plans_in_progress[0] -= 1

    @app.post("/simulations", status_code=202)
    async def submit(body: SimulationRequest, request: Request) -> Job:
        """Start a simulated run in the background. Poll ``/simulations/{id}`` for its result."""
        settle_jobs()
        if sum(job.pending for job in jobs.values()) >= max_pending_jobs:
            raise HTTPException(status_code=429, detail="too many simulations queued or running")
        future = request.app.state.executor.submit(work.run_job, body)
        entry = _JobEntry(uuid.uuid4().hex, body, datetime.now(UTC), future)
        jobs[entry.id] = entry
        finished = [job_id for job_id, job in jobs.items() if not job.pending]
        for job_id in finished[: max(0, len(finished) - MAX_KEPT_JOBS)]:
            del jobs[job_id]
            traces.discard(job_id)
        return entry.view(traces)

    @app.get("/simulations")
    async def list_jobs() -> list[Job]:
        """Every job kept, oldest first."""
        settle_jobs()
        return [job.view(traces) for job in jobs.values()]

    @app.get("/simulations/{job_id}")
    async def job(job_id: str) -> Job:
        settle_jobs()
        entry = jobs.get(job_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"no simulation {job_id!r}")
        return entry.view(traces)

    @app.get(
        "/simulations/{job_id}/trace",
        response_class=Response,
        responses={
            200: {
                "description": "The replay trace of a finished job, as JSON (gzip if accepted).",
                "content": {"application/json": {"schema": TRACE_JSON_SCHEMA}},
            },
            404: {"description": "No such job, or the job has no trace."},
            409: {"description": "The job has not finished."},
            410: {"description": "The trace was dropped to make room for later ones."},
        },
    )
    async def trace(job_id: str, request: Request) -> Response:
        """The replay trace of a job submitted with ``record_trace`` (D33): the map, every
        robot's recorded visits, the orders, the event log, the KPIs and the conflicts.

        The trace is stored compressed and sent as it is to clients that accept gzip."""
        settle_jobs()
        entry = jobs.get(job_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"no simulation {job_id!r}")
        if entry.future is not None:  # still queued or running (or finished this instant)
            raise HTTPException(status_code=409, detail="the simulation has not finished")
        status = entry.trace_status(traces)
        if status == "evicted":
            raise HTTPException(
                status_code=410,
                detail="the trace was dropped to make room for later ones; run the job again",
            )
        if status == "too_large":
            raise HTTPException(
                status_code=404, detail="the trace was larger than the service keeps"
            )
        data = traces.get(job_id)
        if data is None:
            reason = (
                "the simulation failed"
                if entry.error is not None
                else "the simulation was not asked to record a trace (record_trace)"
            )
            raise HTTPException(status_code=404, detail=f"no trace: {reason}")
        headers = {"Vary": "Accept-Encoding"}
        if accepts_gzip(request.headers.get("accept-encoding")):
            headers["Content-Encoding"] = "gzip"
        else:
            data = await run_in_threadpool(gzip.decompress, data)
        return Response(content=data, media_type="application/json", headers=headers)

    return app


app = create_app()
"""The service with its default settings, for ``uvicorn amr_optimizer.api.app:app``."""
