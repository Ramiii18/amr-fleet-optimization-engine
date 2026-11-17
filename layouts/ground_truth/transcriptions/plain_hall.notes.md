# Notes: plan.layout.json (synthetic plan transcription)

## What the plan shows (step 1)
- Image 408 x 540 px. Title says it is a synthetic plan invented for testing. Plan drawn with north/orientation
  unmarked; row 0 of the grid = top of the image.
- Building outline: a rectangle, dark wall colour (RGB 75,79,84) from x = 48 to 359 px and y = 48 to 383 px
  (measured as the extent of that colour along row y=200 and column x=200). That is 312 x 336 px = 13 m x 14 m.
- Walls: thick (1 m) outer walls on top, left and bottom; 2 m on the right above and below the rightmost rack.
- Interior: one rectangular floor area (x 72-311 px) with
  - a two-way cross aisle along the top (row 2) and along the bottom (row 11), no arrows;
  - four vertical aisles (columns 1, 4, 7, 10) with painted arrows: column 1 up, column 4 down, column 7 up,
    column 10 down (three arrows each, at rows 3, 6/7 and 10);
  - three double-deep rack blocks (columns 2-3, 5-6, 8-9), six bays long (rows 4-9), with a solid dark rack end
    above (row 3) and below (row 10) each block;
  - one single-deep rack of six bays (column 11, rows 4-9) against the right wall, facing aisle column 10.
- Symbols (read from the legend):
  - chargers (green disc) in top-wall niches at columns 2 and 8;
  - candidate positions (dashed square) in top-wall niches at columns 4, 6, 10, in the left-wall niche at row 9,
    and in bottom-wall niches at columns 3, 5, 8;
  - parking spots (orange outlined square) in left-wall niches at rows 5 and 7;
  - pick/drop stations (blue square) in bottom-wall niches at columns 2, 6, 9.
- No columns (the legend's black "Column") were found: no black pixels inside the building.
- No doors or docks are drawn.

## Scale and origin (step 5 measurement)
- Scale bar under the legend, labelled 0 to 5 metres, 5 alternating black/white segments.
  Pixel inspection of the bar's rows (y 476-482): bar runs x = 240 to 360; segment boundaries at 264, 288, 312,
  336. 120 px / 5 m = **24 px per metre** (each segment exactly 24 px).
- Outer top-left corner of the building: **(48, 48) px** (first dark wall pixel in both directions).
- Cross-check: building 312 px x 336 px = 13.0 m x 14.0 m exactly; rack bay divider lines at x = 96, 120, ...,
  336 are all multiples of 24 from 48, so the plan's bays coincide with 1 m cells.

## Cell size (step 2)
- **1.0 m** (default). Every feature measured fits whole 1 m cells: aisles are 24 px (1 m) wide, bays are
  24 x 24 px, wall niches are 1 m, walls are whole metres. Grid: **13 columns x 14 rows**.
- Features that do not fit cells exactly: none found. (Symbols are drawn smaller than a cell; each was placed in
  the cell that contains it.)

## Uncertainties (step 3)
- Aisle directions: arrows are drawn only at three rows per aisle; following the convention, the whole aisle
  between the two cross aisles (rows 3-10) is one-way, including the cells beside the rack ends (rows 3 and 10).
- Cross aisles (rows 2 and 11) have no arrows, so they are two-way.
- The right-hand rack (column 11) is one bay deep; I assumed its bays face aisle column 10 (the only floor beside
  them).
- Whether robots may enter the wall niches other than to reach the symbol in them is not stated; each niche is
  one endpoint cell, so it is a dead end either way.
- The 2 m-thick wall at the right (columns 11-12 at rows 1-3 and 10-12) is treated as not drivable, as drawn.
- Orientation: no north arrow; not needed for the grid.

## Check result (step 4)
`.venv\Scripts\python.exe -m amr_optimizer.layout check ... --robots 6`:
101 nodes (2 charger, 52 intersection, 2 parking, 3 station, 42 storage), 170 edges; "the map is fit for traffic".
Candidate positions (7) are not nodes until assigned.

## Render and compare (step 5)
One render (`plan.compare.png`, 24 px/m, origin 48 48). In the overlay panel the cell lines fall on the wall
edges, the rack bay lines and the niche edges; every symbol sits in its niche cell; the aisle arrows sit in the
one-way columns. I saw no mismatch at the render's resolution, so no second render was made. The render is
small (each cell about 22 px in the overlay), so a sub-cell offset of a few pixels would not be visible.
