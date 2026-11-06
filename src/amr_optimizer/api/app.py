"""The HTTP service (D29): the engine's planning and simulation behind FastAPI.

Every CPU-bound call runs in a process pool and the async endpoints await it, so the event loop
only ever parses, validates and answers. The pool is created when the app starts and shut down
when it stops. The interactive API documentation is served at ``/docs``.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from concurrent.futures import Executor, ProcessPoolExecutor
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import TypeVar

from fastapi import FastAPI, HTTPException, Request

from amr_optimizer.api import work
from amr_optimizer.api.models import (
    TICK_S,
    Health,
    MapDetail,
    MapSummary,
    NodeOut,
    PlanRequest,
    PlanResponse,
)
from amr_optimizer.dispatch import POLICIES
from amr_optimizer.warehouse import PRESETS

T = TypeVar("T")
R = TypeVar("R")


def create_app(*, executor: Executor | None = None, workers: int = 2) -> FastAPI:
    """The service. Without ``executor``, it runs CPU-bound work in its own pool of ``workers``
    processes; tests may pass another executor, which the app then does not shut down."""

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

    return app


app = create_app()
"""The service with its default settings, for ``uvicorn amr_optimizer.api.app:app``."""
