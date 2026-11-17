"""Compile a layout file into a map file, or check whether a map or layout is fit for traffic.

    .venv\\Scripts\\python.exe -m amr_optimizer.layout compile hall.layout.json hall.json
    .venv\\Scripts\\python.exe -m amr_optimizer.layout check hall.layout.json --robots 8
    .venv\\Scripts\\python.exe -m amr_optimizer.layout screen base.layout.json --stations 4 ^
        --chargers 2 --top 4 --out candidates [--baseline ..\\hand.layout.json --template t.json]

``check`` accepts map files and layout files. It prints every rule the map breaks and exits with
1 if there is any (D39), or 2 if the file cannot be read.

``screen`` chooses stations and chargers among the base layout's slots (D43) and writes each
candidate to ``--out``, with ``screening.json`` (the proxies of every candidate, and of the
baseline if given). With ``--baseline`` (a layout path as the experiment file will name it,
relative to ``--out``) and ``--template`` (an experiment file without layouts and baseline), it
also writes ``experiment.json`` comparing the candidates with the baseline.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from amr_optimizer.domain import BatterySpec
from amr_optimizer.layout.compiler import compile_layout
from amr_optimizer.layout.io import load_layout, load_map_file
from amr_optimizer.layout.screening import (
    ScreeningError,
    experiment_for,
    proxies,
    screen,
    write_candidates,
)
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
    screen_ = commands.add_parser("screen", help="choose stations and chargers among the slots")
    screen_.add_argument("base", type=Path, help="a layout file with candidate slots (?)")
    screen_.add_argument("--stations", type=int, required=True)
    screen_.add_argument("--chargers", type=int, required=True)
    screen_.add_argument("--top", type=int, default=4, help="station sets kept by the proxy")
    screen_.add_argument("--out", type=Path, required=True, help="folder for the candidates")
    screen_.add_argument("--baseline", help="the baseline layout, relative to --out")
    screen_.add_argument("--template", type=Path, help="experiment template (JSON)")
    args = parser.parse_args(argv)
    if args.command == "screen":
        return _screen(args)
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


def _screen(args: argparse.Namespace) -> int:
    try:
        base = load_layout(args.base)
        candidates = screen(base, stations=args.stations, chargers=args.chargers, top=args.top)
        written = write_candidates(base, candidates, args.out)
        rows = [
            {
                "name": c.name,
                "file": written[c.name],
                "stations": [list(cell) for cell in c.stations],
                "chargers": [list(cell) for cell in c.chargers],
                "station_round_trip_m": round(c.station_round_trip_m, 6),
                "charger_trip_m": round(c.charger_trip_m, 6),
                "smallest_station_gap_m": round(c.smallest_station_gap_m, 6),
            }
            for c in candidates
        ]
        if args.baseline is not None:
            measured = proxies(load_layout(args.out / args.baseline))
            rows.insert(
                0,
                {
                    "name": "baseline",
                    "file": args.baseline,
                    "station_round_trip_m": round(measured.station_round_trip_m, 6),
                    "charger_trip_m": round(measured.charger_trip_m, 6),
                },
            )
        text = json.dumps({"base": args.base.name, "candidates": rows}, indent=2) + "\n"
        (args.out / "screening.json").write_text(text, encoding="utf-8", newline="\n")
        if args.template is not None:
            if args.baseline is None:
                print("error: --template needs --baseline", file=sys.stderr)
                return 2
            template = json.loads(args.template.read_text(encoding="utf-8"))
            experiment = experiment_for(written, args.baseline, template)
            text = json.dumps(experiment, indent=2) + "\n"
            (args.out / "experiment.json").write_text(text, encoding="utf-8", newline="\n")
    except (OSError, MapValidationError, ScreeningError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    for row in rows:
        print(
            f"{row['name']:>10}  stations {row['station_round_trip_m']:8.2f} m per order, "
            f"chargers {row['charger_trip_m']:8.2f} m per trip  ({row['file']})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
