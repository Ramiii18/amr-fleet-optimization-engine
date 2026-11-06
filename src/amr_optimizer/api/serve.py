"""Command line: run the HTTP service (`amr-api`, D29).

Example (PowerShell, from the repository root, with the ``api`` extra installed):

    .venv\\Scripts\\amr-api.exe --port 8000

Then open http://127.0.0.1:8000/docs for the interactive API documentation.
"""

import argparse
from collections.abc import Sequence

import uvicorn

from amr_optimizer.api.app import create_app


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="amr-api", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="the interface to listen on; the default accepts local connections only",
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workers", type=int, default=2, help="worker processes for planning")
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    uvicorn.run(create_app(workers=args.workers), host=args.host, port=args.port)
    return 0
