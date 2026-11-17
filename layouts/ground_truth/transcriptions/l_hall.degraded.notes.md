# plan.layout.json: transcription notes

Synthetic plan ("invented for testing, not a real site"). Transcribed from the image alone, by eye.

## What the plan shows
- Outline: a rectangle of 25.00 m x 22.00 m (dimension lines on top and left), with the upper-right part
  (about x 12-24 m, y 0-10 m) solid wall / no access. Usable floor is an L shape: an upper hall on the
  left (x 1-11 m) and a lower hall across the full width (x 1-23 m).
- Outer walls about 1 m thick; the top wall is 2 m thick with niches in its lower metre, the bottom wall
  is 2 m thick with niches in its upper metre. The left wall has niches.
- Upper hall: top cross aisle (row 2), three double racks (2 bays wide x 5 bays long, solid ends top and
  bottom) and one single-column rack of 5 bays set into the right wall. Aisles x = 1, 4, 7, 10 m, painted
  arrows up, down, up, down.
- Middle: a 2 m wide cross aisle (rows 10-11) joining both halls, with four 1 m dark blocks hanging into
  row 10 (at x = 3, 9, 15, 21 m): read as columns / wall stubs, all `#`.
- Lower hall: seven double racks (2 x 5 bays, solid ends) and one single-column rack of 5 bays set into the
  right wall; aisles x = 1, 4, 7, 10, 13, 16, 19, 22 m with arrows alternating up, down, ... down; bottom
  cross aisle (row 19).
- Symbols from the legend: 2 chargers (top niches, cols 2 and 10), 3 parking spots (left niches, rows 5,
  14, 16), 2 pick/drop stations (bottom niches, cols 2 and 14), 8 candidate positions (top niches cols 5
  and 8; left niche row 7; bottom niches cols 5, 8, 11, 17, 20).
- Scale bar at bottom right: 0 to 5 m.

## Scale, origin, rotation
- The plan is slightly tilted: the top edge drops about 10 px over about 543 px and the left edge leans
  the same way, so the drawing is rotated about 1.1 deg clockwise. Straightened with `--rotate-deg 1.1`.
- Scale: the 5 m scale bar is about 110 px (about 22 px/m); the 25 m building edge is about 543-545 px and
  the 22 m edge about 475-480 px, i.e. 21.7-21.9 px/m. Used **21.8 px/m**.
- Origin: the outer top-left corner was about (55, 45) in the tilted original. The first render with that
  origin showed the grid about 10 px too high, because rotating the image moves the corner; corrected to
  **(56, 56)** in the straightened image, after which the grid edges fell on the building edges.

## Cell size
- **1.0 m.** Aisles, rack bays, wall thickness, niches and the dark stubs all measure about one 22 px
  module, and the 25 m x 22 m outline divides into whole cells. No feature found that does not fit
  1 m cells, within what can be judged by eye. Grid: 25 columns x 22 rows.

## Decisions and uncertainties
- **Single-column racks (col 11 upper, col 23 lower)** have no drawn solid ends. In the straightened plan
  the cells above and below them look like wall (the white hall stops one metre to their left), so they
  are `#`. My first draft made them floor, which left the end bays with two floor neighbours (checker
  failure); the render showed they are wall. If those cells are really floor, the end bays could not be
  `S` as drawn.
- **Row 10 stubs** at x = 3, 9, 15, 21 m: drawn in the same dark grey as walls rather than the black of
  the legend's column, so they may be wall stubs; `#` either way. Their size (about 1 m) is estimated.
- **Arrow extent**: arrows are applied to the whole aisle between crossings (rows 3-9 upper, rows 12-18
  lower); cross aisles (rows 2, 10, 11, 19) are two-way, as no arrows are drawn there.
- **Niche rows**: top niches read as row 1 and bottom niches as row 20 (the top and bottom walls look
  2 m thick). Left niches in column 0 (the left wall is 1 m thick).
- Candidate positions are `?` and do not show as nodes in the compiled map, so their cells were checked
  only against the grid overlay.
- Rotation (1.1 deg) and scale (21.8 px/m) are estimates by eye; an error of 0.2 px/m would shift the
  far edge by about 5 px (a quarter cell), which the render does not show.
