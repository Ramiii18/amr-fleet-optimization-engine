"""The HTTP service (Phase 2, P4, D29). Needs the ``api`` extra: FastAPI and Uvicorn."""

from amr_optimizer.api.app import create_app

__all__ = ["create_app"]
