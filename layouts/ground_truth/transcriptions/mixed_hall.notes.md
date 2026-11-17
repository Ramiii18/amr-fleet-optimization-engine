# plan.layout.json: transcription notes

Source: `plan.png` (synthetic floor plan, 816 x 732 px). Transcription only. It is an approximation, not an exact model.

## What the plan shows
- **Outline:** one rectangular building, 30 m x 22 m with outer walls included. Two halls are joined along an open edge:
  - **Left hall** (interior x 1..14, y 2..18). It has four vertical rack rows, 13 bays long (rows 4..16), with solid
    rack ends top and bottom (rows 3 and 17). Three rows are two bays deep (cols 2-3, 5-6, 8-9). The fourth is one
    bay deep (col 11), with a solid strip behind it (col 12). There are four one-way vertical aisles: col 1 up, col 4
    down, col 7 up, col 10 down. Cross aisles without arrows run along row 2 (top) and row 18 (bottom). Cols 13-14
    form a 2 m two-way open strip, which is also the link to the right hall.
  - **Right hall** (interior x 15..28, y 3..19). There are two horizontal rack rows, each two bays deep and 11 bays
    long (cols 16..26, rows 4-5 and 7-8), with solid ends at cols 15 and 27. There are three one-way horizontal
    aisles: row 3 east, row 6 west, row 9 east. A solid wall strip runs along row 10 (cols 15..27). Col 28 is a
    two-way vertical aisle with no arrows. Below row 10 is an open area (rows 11..19, cols 13..28) with four
    columns at (x18,y13), (x24,y13), (x18,y16) and (x24,y16).
- **Symbols, read from the legend:**
  - chargers (green circle) in top-wall niches at (x2,y1) and (x8,y1);
  - candidate positions (dashed square) at (x5,y1), (x3,y19), (x6,y19) and (x9,y19);
  - parking (orange square) in the left wall at (x0,y6) and (x0,y10), and in the right wall at (x29,y12),
    (x29,y14) and (x29,y16);
  - pick/drop stations (blue square) in bottom-wall niches at (x17,y20), (x20,y20), (x23,y20) and (x26,y20).
- **Orientation:** there is no north arrow. Row 0 is the top of the image.
- No doors or docks are drawn apart from the symbol niches.

## Scale and origin
- **Scale bar:** "0 ... metres ... 5". I measured it with pixel inspection on row y=671. The bar runs from x=648
  to x=768, and the alternating black/white segments change at x = 648, 672, 696, 720, 744 and 768/769. That makes
  five segments of 24 px, so **24 px per metre** (120 px / 5 m).
- **Origin:** I took the outer top-left corner of the building from the wall-colour pixels (RGB 75,79,84) in the
  plan area. The wall spans x 48..767 and y 48..575, so the **origin is (48, 48)**. The building is 720 x 528 px,
  which is 30.0 m x 22.0 m.

## Cell size
**1.0 m.** This is the default, and every feature on the plan sits on a 24 px (1 m) lattice from the origin: rack
bays are 24 px, aisles are 24 px (1 cell), and the strip at cols 13-14 is 48 px (2 cells). I confirmed this by
classifying every 24 px cell from its pixel colours: each cell had a single clear class. Grid: **30 columns x 22
rows**.

Features that do not fit the cells exactly:
- The columns are about 14 px (~0.6 m) square, smaller than a cell. Each became one `#` cell.
- The arrows are painted in only some aisle cells. Following the convention, each whole aisle between its two
  crossings is one-way.

## Decisions and uncertainties
1. **Extent of the one-way aisles.**
   - Left hall: rows 3..17 of cols 1, 4, 7 and 10 are one-way. Rows 2 and 18 are the crossings and stay two-way.
   - Right hall: cols 15..27 of rows 3, 6 and 9 are one-way, because col 15 carries a painted arrow. Col 14 (the
     open strip) and col 28 (the vertical aisle) are the crossings and stay two-way.
   - Rows 3 and 17 of the left-hall aisles lie beside the solid rack ends, not the bays. I treated them as part of
     the aisle (one-way), not as crossings.
2. **Col 28 is two-way.** No arrows are drawn there, so I treated it as an unmarked aisle and made it two-way.
3. **The strip at cols 13-14 of the left hall** is open floor with no racks or arrows, so it is two-way floor.
4. **The fourth left rack (col 11)** is drawn one bay deep, with dark fill at col 12. I read the dark fill as a solid
   back or wall, not as bays.
5. **The parking spots at (x0,y6) and (x0,y10)** sit beside the one-way aisle at col 1. This is allowed, because an
   endpoint links both ways to its floor neighbour. Leaving a spot and continuing still has to follow the
   up-direction of that aisle.
6. **Candidate positions** were transcribed as `?`. They are not part of the compiled map until they are assigned.
7. **No doors or docks** are shown, so the building is treated as closed.

## Check and render
- `check --robots 6`: 433 nodes (2 charger, 287 intersection, 5 parking, 4 station, 135 storage) and 1002 edges.
  Result: "the map is fit for traffic".
- Render 1 (`plan.compare.png`, 24 px/m, origin 48 48): in the overlay, the cell grid lines up with the walls,
  racks, aisles, columns and every symbol. I saw no mismatch at the render's resolution. Only one render was made.
