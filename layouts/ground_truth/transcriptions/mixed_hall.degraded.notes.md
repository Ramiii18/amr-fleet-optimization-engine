# plan.layout.json: transcription notes

Transcribed by eye from `plan.png` only (no pixel-reading code). An approximation, not an exact copy.

## What the plan shows
- Rectangular building, dimensioned 30.00 m x 22.00 m, with a 0 to 5 m scale bar. The drawing is tilted
  about 2 degrees counter-clockwise.
- Outer walls are one cell thick on the left and right. The top wall is 2 m thick over the left part
  (cols 0-14) and 3 m thick over the right part (cols 15-29). The bottom wall is 3 m thick on the left
  (rows 19-21, cols 0-14) and 2 m on the right (rows 20-21, cols 15-29).
- Left area (cols 1-14, rows 2-18): four vertical racks with end caps (rows 3 and 17) and 13 bays each
  (rows 4-16). Racks 1-3 are two bays wide (cols 2-3, 5-6, 8-9). Rack 4 is one bay wide (col 11) with
  a solid dark back (col 12, rows 3-17). Aisles: col 1 up, col 4 down, col 7 up, col 10 down (painted
  arrows), cross aisles at rows 2 and 18 (two-way). Cols 13-14 are an unmarked two-way strip.
- Right block (cols 15-28, rows 3-10): two horizontal racks, each 11 bays long (cols 16-26) and two bays
  deep (rows 4-5 and 7-8), end caps at cols 15 and 27. Aisles: row 3 east, row 6 west, row 9 east.
  A solid bar at row 10, cols 15-27. Col 28 is an unmarked two-way aisle down the right side.
- Open area (cols 13-28, rows 11-19) with four columns at (col 18, row 13), (24, 13), (18, 16), (24, 16).
- Legend symbols: chargers in top-wall niches at (col 2, row 1) and (8, 1); candidate positions at
  (5, 1) and in bottom-wall niches at (3, 19), (6, 19), (9, 19); parking spots in the left wall at
  (0, 6), (0, 10) and in the right wall at (29, 12), (29, 14), (29, 16); pick/drop stations in
  bottom-wall niches at (17, 20), (20, 20), (23, 20), (26, 20).

## Scale and alignment
- Scale bar: 0 to 5 m spans about 108-110 px, so about 21.6-22 px/m. The building outline (about 650 px
  for 30 m and 475 px for 22 m along the tilted edges) gives about 21.6-21.7 px/m. Used **21.65 px/m**.
- Tilt: the top and bottom edges rise about 25 px over 650 px (about 2.2 deg), the side edges lean about
  12-17 px over 475 px (1.5-2 deg). Used `--rotate-deg -2` (rotate clockwise to straighten).
- Origin: first render with origin (54, 58), computed for a rotation about the centre without canvas
  growth, was off by about +12 px in x and +9 px in y (the renderer appears to enlarge the canvas when it
  rotates). Second render with **origin (66, 68)** in the straightened image lines up.

## Cell size
1.0 m (default). Rack bays measure about one metre on the plan, aisles are one bay wide, and all walls,
niches and symbols fall on whole metres, so 1 m cells reproduce the drawing without splitting features.
Features that do not fill a cell: the four columns are drawn smaller than 1 m (made a full `#` cell);
the symbol squares are smaller than their niches.

## Uncertainties
- Exact tilt (estimates ranged 1.5-2.2 deg) and origin are eyeballed; residual misalignment of a few
  pixels is possible, particularly at the far corners.
- Whether row 2 at cols 13-14 is floor (taken as floor, joining the left top cross aisle to the strip)
  and whether the right block's top wall is really one row deeper than the left (read as yes, from the
  step at about x = 375 px).
- The step in the bottom wall is read at col 15 (cols 13-14 end at row 18, cols 15-28 continue to row 19).
- Rack 4's dark column (col 12) is read as a solid rack back, not drivable.
- Cols 13-14, col 28, rows 2 and 18, and the open area have no arrows and are taken as two-way.
- One-way direction is applied to the whole aisle between crossings (col 1/4/7/10 rows 3-17; rows 3/6/9
  cols 15-27), not just to the cells with a painted arrow.
- Parking spots sit in niches cut through the one-cell outer wall (cols 0 and 29), so the outer wall
  column is interrupted there.
- Candidate positions (`?`) are not drawn as nodes in the compiled map, so their placement was checked
  only against the plan underlay.
