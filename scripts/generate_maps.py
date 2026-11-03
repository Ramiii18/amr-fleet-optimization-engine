"""Regenerate the example maps in ``scenarios/maps`` from the generator presets.

Usage (from the repository root):
    .venv\\Scripts\\python.exe scripts\\generate_maps.py

The committed maps are checked against the generator by the test suite, so after a
generator change, rerun this script and commit the updated maps.
"""

from pathlib import Path

from amr_optimizer.warehouse import PRESETS, NodeKind, generate_grid_warehouse, save_graph

REPO_ROOT = Path(__file__).resolve().parents[1]
MAPS_DIR = REPO_ROOT / "scenarios" / "maps"


def main() -> None:
    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    for name, spec in PRESETS.items():
        graph = generate_grid_warehouse(spec)
        path = MAPS_DIR / f"{name}.json"
        save_graph(graph, path)
        counts = ", ".join(f"{kind.value} {len(graph.nodes_of_kind(kind))}" for kind in NodeKind)
        print(
            f"{path.relative_to(REPO_ROOT)}: {len(graph)} nodes, {graph.num_edges} edges ({counts})"
        )


if __name__ == "__main__":
    main()
