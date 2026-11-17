"""Compile a layout file into a map file, or check whether a map or layout is fit for traffic.

    .venv\\Scripts\\python.exe -m amr_optimizer.layout compile hall.layout.json hall.json
    .venv\\Scripts\\python.exe -m amr_optimizer.layout check hall.layout.json --robots 8

``check`` accepts map files and layout files. It prints every rule the map breaks and exits with
1 if there is any (D39), or 2 if the file cannot be read.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

from amr_optimizer.domain import BatterySpec
from amr_optimizer.layout.compiler import compile_layout
from amr_optimizer.layout.io import load_layout, load_map_file
from amr_optimizer.layout.wellformed import check_well_formed
from amr_optimizer.warehouse import MapValidationError, WarehouseGraph, save_graph


def _summary(graph: WarehouseGraph) -> str:
    kinds = Counter(node.kind.value for node in graph)
    counts = ", ".join(f"{count} {kind}" for kind, count in sorted(kinds.items()))
    return f"{len(graph)} nodes ({counts}), {graph.num_edges} edges"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m amr_optimizer.layout", description=(__doc__ or "").splitlines()[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    compile_ = commands.add_parser("compile", help="write the map file of a layout file")
    compile_.add_argument("layout", type=Path)
    compile_.add_argument("map", type=Path)
    check = commands.add_parser("check", help="check a map or layout file for traffic")
    check.add_argument("map", type=Path, help="a map file or a layout file")
    check.add_argument("--robots", type=int, help="also check the holding spots for this fleet")
    check.add_argument(
        "--batteries", action="store_true", help="also check chargers and the battery threshold"
    )
    check.add_argument(
        "--put-away", action="store_true", help="orders may end at holding spots (4N - 2)"
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "compile":
            graph = compile_layout(load_layout(args.layout))
            save_graph(graph, args.map)
            print(f"{args.map}: {_summary(graph)}")
            return 0
        graph = load_map_file(args.map)
    except (OSError, MapValidationError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.robots is not None and args.robots < 1:
        parser.error("--robots must be at least 1")
    report = check_well_formed(
        graph,
        robots=args.robots,
        battery=BatterySpec() if args.batteries else None,
        dropoffs_hold=args.put_away,
    )
    print(f"{args.map}: {_summary(graph)}")
    print(report.describe())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
