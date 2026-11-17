# From a floor plan to a layout file

This is the procedure for turning a floor plan (an image, a PDF page or a sketch) into a layout file
that the engine can run. It is done by a person, or by a model during a working session, and checked
by rendering. The engine itself never reads pictures and never calls a model (D37, D42).

A layout made from a picture is an **approximation of a building**: axis-aligned cells of one size,
with what the plan shows and what the person reading it understood. Say what is uncertain; never
present a transcription as exact.

**Privacy.** Real plans and everything made from them stay in `layouts/private/`, which git ignores.
They are never committed, and results computed on them are reported in the conversation only. A
layout's `description` never names or identifies a real site.

## The layout file

```json
{
  "format": "amr-layout",
  "version": 1,
  "description": "Synthetic hall: two one-way aisles between two cross aisles",
  "cell_m": 1.0,
  "grid": [
    "########",
    "#C####P#",
    "#......#",
    "#^##v###",
    "#^S#vS##",
    "#^##v###",
    "#^S#vS##",
    "#^##v###",
    "#......#",
    "#T#T#?##",
    "########"
  ]
}
```

One character per square cell of `cell_m` metres. Row 0 is the top of the plan; every row has the
same length.

| Char | Meaning |
|---|---|
| `#` | not drivable: wall, rack frame or rack end, column, outside the building, anything a robot cannot drive on |
| `.` | floor, two-way |
| `^` `v` `<` `>` | floor, one-way: along this arrow's axis robots may only move in its direction |
| `S` | storage location (one rack bay) |
| `T` | station: a pick-up or drop-off point |
| `C` | charger |
| `P` | parking spot |
| `?` | candidate position: empty now, may later become a station, charger or parking spot |

**How the cells become a map.** Every floor cell and every endpoint (`S T C P`, and `?` once
assigned) is a node; neighbours share an edge only up, down, left and right.
- Two floor cells are joined both ways, unless one of them is an arrow on the line that joins them:
  then only in the arrow's direction. Arrows on one line that point against each other are an
  error. An arrow does not restrict moving sideways out of it.
- An endpoint is joined, both ways, to every floor cell beside it, and never to another endpoint.

**What the traffic planner needs** (the checker enforces it):
- **Every endpoint is a dead end:** exactly one floor neighbour. So a storage cell at the end of a
  rack that also touches a cross aisle must not be `S`: rack ends are `#`. Two endpoints side by side
  are fine, since they are not joined.
- **Every floor cell can reach every other**, respecting the arrows.
- At least one storage location and one station.

## Conventions for reading a plan

- **The grid starts at the outer top-left corner of the building**, walls included: the outer walls
  are the first and last rows and columns of `#`. Parts of the rectangle outside the building are `#`.
- **Cell size.** 1.0 m unless there is a reason to choose otherwise. The cell size is also the
  closest two robots can be (one robot per cell). Count cells from the scale bar, not by eye.
- **Racks.** Each rack bay drawn on the plan is one storage location `S`. The solid end of a rack is
  `#`. A rack between two aisles is usually two bays deep: each bay faces its own aisle.
- **Columns** are `#`, even when smaller than a cell.
- **Symbols** (stations, chargers, parking spots, candidate positions) are read from the plan's own
  legend. A symbol in a niche of a wall is an endpoint in that niche's cell.
- **One-way aisles.** Painted arrows mark the direction; the whole aisle between two crossings runs
  that way, not only the cells with a painted arrow. Cross aisles without arrows are two-way.
- **Open areas** without racks are floor.

## The procedure

1. **Read the plan.** Write down what you see: the outline and its size, walls, columns, rack rows and
   their orientation, aisles and their direction, docks and doors, stations, chargers, parking spots,
   and the **scale** (a scale bar or written dimensions) and orientation. **If there is no scale, ask
   the owner. Never guess a scale.**
2. **Choose the cell size**, 1.0 m by default, and say why. List every feature that does not fit the
   cells exactly (an aisle narrower than a cell, a rack that is not a whole number of cells).
3. **List the uncertainties** in a notes file next to the layout (`<name>.notes.md`): ambiguous
   doors, rack orientation, areas that may not be drivable, symbols you could not read.
4. **Write the layout file.** Then compile and check it, and fix every violation:

   ```powershell
   .venv\Scripts\python.exe -m amr_optimizer.layout check <name>.layout.json --robots 6
   ```

   When the checker refuses a cell, look at the plan again at that place before changing the
   grid: the refusal usually points at a misread neighbour. Never block or unblock a cell only to
   satisfy the checker. (This comes from the second round's plain hall, where a rack end read as
   floor was "repaired" by blocking two rack bays; it is untested guidance.)

5. **Render and compare.** Measure the plan's scale in pixels per metre from its scale bar, and the
   pixel of the building's outer top-left corner. Then:

   ```powershell
   .venv\Scripts\python.exe scripts\render_layout.py <name>.layout.json --out <name>.compare.png `
       --plan <plan>.png --px-per-m <pixels per metre> --origin-px <x> <y>
   ```

   If the plan is tilted, add `--rotate-deg <degrees>` (counter-clockwise) to straighten it before
   it is drawn; `--origin-px` then refers to the straightened image.

   **Open the comparison image and look at it.** It shows the plan, the compiled map, and the map
   with your cells drawn over the plan. List every mismatch: a missing aisle, a shifted rack, a wrong
   direction, a symbol in the wrong cell, cells that do not line up with the plan. Fix and render
   again, **at most three times**. Then report what still differs. Never say that the layout matches
   without having looked at the render.
6. **Stop and show** the render, the notes and the remaining differences to the owner. Nothing is
   built on a layout until the owner confirms it.

## How well does this work?

The procedure was measured on synthetic plans with known answers, transcribed by an agent that saw
only the plan image and this document: per-class agreement of cells, scale error, whether the result
passes the checker, and the difference in simulated completion time that the errors cause. The
results are in the [benchmarks](benchmarks.md); they describe these plans, not plans in general.
