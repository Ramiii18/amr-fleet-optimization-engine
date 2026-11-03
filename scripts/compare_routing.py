"""Compare Dijkstra and A* (Euclidean, Manhattan) on the generated warehouse presets.

For every map, the same seeded random queries (between non-intersection locations, i.e.
storage, stations, chargers and parking) are run with every algorithm.

Measured:

- nodes expanded per query: deterministic, hardware independent;
- runtime per query: each query is repeated ``--repeats`` times and its fastest run is
  kept (the usual estimator that filters out OS noise). Median and P95 over queries are
  reported. Runtimes depend on the machine; the environment is printed with the results.

The script fails (exit code 1) if algorithms disagree on any route length.

Usage (from the repository root):
    .venv\\Scripts\\python.exe scripts\\compare_routing.py [--queries 300] [--repeats 5] [--seed 42]
"""

import argparse
import math
import platform
import random
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import date
from importlib.metadata import version
from pathlib import Path

from amr_optimizer.routing import SearchResult, astar, euclidean, manhattan, shortest_path
from amr_optimizer.warehouse import (
    PRESETS,
    NodeId,
    NodeKind,
    WarehouseGraph,
    generate_grid_warehouse,
)

Search = Callable[[WarehouseGraph, NodeId, NodeId], SearchResult]

ALGORITHMS: dict[str, Search] = {
    "Dijkstra": shortest_path,
    "A* Euclidean": lambda graph, s, t: astar(graph, s, t, euclidean),
    "A* Manhattan": lambda graph, s, t: astar(graph, s, t, manhattan),
}


def p95(values: list[float]) -> float:
    return statistics.quantiles(values, n=100, method="inclusive")[94]


def git_commit() -> str:
    try:
        output = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parent,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return output.stdout.strip()


def run(queries: int, repeats: int, seed: int) -> int:
    print(f"Routing comparison, {date.today().isoformat()}")
    print(
        f"Environment: Python {platform.python_version()}, {platform.platform()}, "
        f"CPU {platform.processor() or 'unknown'}, "
        f"amr-fleet-optimization-engine {version('amr-fleet-optimization-engine')} "
        f"(commit {git_commit()})"
    )
    print(f"Settings: {queries} queries per map, {repeats} repeats per query, seed {seed}")
    print()
    print("| Map | Nodes | Edges | Algorithm | Mean expanded | P95 expanded | Median µs | P95 µs |")
    print("|---|---:|---:|---|---:|---:|---:|---:|")

    mismatches = 0
    for map_name, spec in PRESETS.items():
        graph = generate_grid_warehouse(spec)
        endpoints = [node.id for node in graph if node.kind is not NodeKind.INTERSECTION]
        rng = random.Random(seed)
        pairs = [tuple(rng.sample(endpoints, 2)) for _ in range(queries)]

        expanded: dict[str, list[float]] = {name: [] for name in ALGORITHMS}
        micros: dict[str, list[float]] = {name: [] for name in ALGORITHMS}
        for source, target in pairs:
            lengths: dict[str, float] = {}
            for name, search in ALGORITHMS.items():
                search(graph, source, target)  # warm-up
                fastest_ns = math.inf
                for _ in range(repeats):
                    start = time.perf_counter_ns()
                    result = search(graph, source, target)
                    fastest_ns = min(fastest_ns, time.perf_counter_ns() - start)
                if result.route is None:
                    print(f"ERROR: {name} found no route {source}->{target}", file=sys.stderr)
                    return 1
                lengths[name] = result.route.length_m
                expanded[name].append(result.expanded)
                micros[name].append(fastest_ns / 1000)
            reference = lengths["Dijkstra"]
            for name, length in lengths.items():
                if not math.isclose(length, reference, rel_tol=1e-9):
                    mismatches += 1
                    print(f"MISMATCH {map_name} {source}->{target}: {name} {length} vs {reference}")

        for name in ALGORITHMS:
            print(
                f"| {map_name} | {len(graph)} | {graph.num_edges} | {name} "
                f"| {statistics.fmean(expanded[name]):.1f} | {p95(expanded[name]):.0f} "
                f"| {statistics.median(micros[name]):.1f} | {p95(micros[name]):.1f} |"
            )

    print()
    if mismatches:
        print(f"FAILED: {mismatches} route-length mismatches")
        return 1
    print("All algorithms returned identical route lengths for every query.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--queries", type=int, default=300)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    sys.exit(run(args.queries, args.repeats, args.seed))


if __name__ == "__main__":
    main()
