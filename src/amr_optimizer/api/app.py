"""The HTTP service (D29): the engine's planning and simulation behind FastAPI.

Every CPU-bound call runs in a process pool and the async endpoints await it, so the event loop
only ever parses, validates and answers. The pool is created when the app starts and shut down
when it stops. The interactive API documentation is served at ``/docs``.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from concurrent.futures import Executor, Future, ProcessPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from typing import TypeVar

from fastapi import FastAPI, HTTPException, Request

from amr_optimizer.api import work
from amr_optimizer.api.models import (
    TICK_S,
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
)
from amr_optimizer.dispatch import POLICIES
from amr_optimizer.warehouse import PRESETS

T = TypeVar("T")
R = TypeVar("R")

MAX_PENDING_JOBS = 8  # queued or running; a new job beyond that is refused (429)
MAX_KEPT_JOBS = 100  # finished jobs kept for polling; the oldest are forgotten first


@dataclass(frozen=True, slots=True)
class _JobEntry:
    id: str
    request: SimulationRequest
    future: "Future[SimulationOutcome]"
    submitted_at: datetime

    def view(self) -> Job:
        future = self.future
        status: JobStatus = "running" if future.running() else "queued"
        result = error = None
        if future.done():
            failure = None if future.cancelled() else future.exception()
            if future.cancelled() or failure is not None:
                status = "failed"
                error = "cancelled" if failure is None else f"{type(failure).__name__}: {failure}"
            else:
                status, result = "done", future.result()
        return Job(
            id=self.id,
            status=status,
            submitted_at=self.submitted_at,
            request=self.request,
            result=result,
            error=error,
        )


def create_app(
    *,
    executor: Executor | None = None,
    workers: int = 2,
    max_pending_jobs: int = MAX_PENDING_JOBS,
) -> FastAPI:
    """The service. Without ``executor``, it runs CPU-bound work in its own pool of ``workers``
    processes; tests may pass another executor, which the app then does not shut down. At most
    ``max_pending_jobs`` simulation jobs are queued or running at a time."""
    jobs: dict[str, _JobEntry] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = executor or ProcessPoolExecutor(max_workers=workers)
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
        return MapDetail(name=name, nodes=nodes)

    @app.get("/policies")
    async def policies() -> list[str]:
        return sorted(POLICIES)

    @app.post("/plan")
    async def plan(body: PlanRequest, request: Request) -> PlanResponse:
        """One dispatch decision: which idle robot starts which pending order now."""
        return await in_worker(request, work.decide, body)

    @app.post("/simulations", status_code=202)
    async def submit(body: SimulationRequest, request: Request) -> Job:
        """Start a simulated run in the background. Poll ``/simulations/{id}`` for its result."""
        if sum(not job.future.done() for job in jobs.values()) >= max_pending_jobs:
            raise HTTPException(status_code=429, detail="too many simulations queued or running")
        future = request.app.state.executor.submit(work.simulate_job, body)
        entry = _JobEntry(uuid.uuid4().hex, body, future, datetime.now(UTC))
        jobs[entry.id] = entry
        finished = [job_id for job_id, job in jobs.items() if job.future.done()]
        for job_id in finished[: max(0, len(finished) - MAX_KEPT_JOBS)]:
            del jobs[job_id]
        return entry.view()

    @app.get("/simulations")
    async def list_jobs() -> list[Job]:
        """Every job kept, oldest first."""
        return [job.view() for job in jobs.values()]

    @app.get("/simulations/{job_id}")
    async def job(job_id: str) -> Job:
        entry = jobs.get(job_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"no simulation {job_id!r}")
        return entry.view()

    return app


app = create_app()
"""The service with its default settings, for ``uvicorn amr_optimizer.api.app:app``."""
