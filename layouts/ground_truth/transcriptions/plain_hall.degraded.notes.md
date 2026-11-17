# plan.layout.json: transcription notes

Synthetic plan ("SYNTHETIC FLOOR PLAN - invented for testing"). Transcribed by eye from plan.png only;
it is an approximation, not an exact copy.

## What the plan shows
- Outline: one rectangle, dimensioned 13.00 m wide and 14.00 m high, dark outer wall about 1 m thick.
- The image is slightly tilted (building rotated counter-clockwise by roughly 1.5 degrees, top edge
  rising to the right) and blurred/noisy; the title text is cut off on the right.
- Racks: three double-deep rack blocks (2 bays wide, 6 bays long, vertical), each with a dark solid end
  at the top and the bottom; plus one single-deep rack of 6 bays against the right wall, drawn with
  no dark ends.
- Aisles: four vertical aisles (left of each rack block and between the last block and the single rack),
  with painted arrows: aisle 1 up, aisle 2 down, aisle 3 up, aisle 4 down. Two cross aisles (top and
  bottom) with no arrows, so two-way.
- Top wall: niches with 2 chargers (green circles) and 3 candidate positions (dashed squares).
- Bottom wall: niches with 3 pick/drop stations (blue squares) and 3 candidate positions.
- Left wall: niches with 2 parking spots (orange squares) and 1 candidate position, separated by two
  black squares (legend: column).
- No doors, docks or interior columns visible.

## Scale and origin
- Scale bar 0..5 m spans about 109 px (about 21.8 px/m); the 13.00 m dimension line spans about 285 px
  (about 21.9 px/m). Used 21.9 px/m. The 14.00 m dimension agrees within a few pixels.
- Rotation: --rotate-deg -1.5 (clockwise 1.5 degrees to straighten), estimated from the slope of the
  top and left wall edges.
- Origin (outer top-left corner of the building, in the straightened image): first guessed (50, 43),
  corrected after render 2 to (55, 51) by reading the offset of chargers, stations and walls in the
  overlay.
- Renders: 1) 22 px/m, origin (45, 45), no rotation (showed the tilt); 2) rotate -1.5, origin (50, 43)
  (about 5 px right / 8 px down offset); 3) final: 21.9 px/m, origin (55, 51), rotate -1.5.

## Cell size
1.0 m (the default). The plan is drawn on a 1 m module: aisles, rack bays, wall thickness and niches
are each one cell wide, and 13 x 14 m gives a 13 x 14 grid. No feature was found that is narrower
than a cell or a non-whole number of cells, as far as the blurred image lets me tell.

## Grid
13 columns x 14 rows. Row 0 / row 13 and column 0 / column 12 are the outer walls; row 1 and row 12
are the top and bottom wall niche rows; rows 2 and 11 are the cross aisles; rows 3 and 10 are rack
ends; rows 4-9 are the rack bays.

## Decisions and uncertainties
1. **Single rack against the right wall (column 11).** The plan draws 6 tan bays (rows 4-9) with white
   floor directly above (row 3) and below (row 10) and no dark rack ends. Taken literally, the top and
   bottom bays would touch two floor cells and fail the dead-end rule. Following the procedure ("rack
   ends are #"), I made rows 4 and 9 of column 11 `#` and kept rows 5-8 as `S`, and kept the white
   cells at rows 3 and 10 as floor (short two-way stubs). This loses 2 storage locations versus the
   drawing. The alternative (keep 6 bays, make rows 3 and 10 `#`) is equally defensible.
2. Black squares in the left wall (rows 6 and 8) are read as columns (legend); `#` either way.
3. Left wall at row 10 (below the left candidate): blurry; read as wall.
4. Arrow directions are read from 2-3 arrows per aisle; the arrows are blurry but consistent within each
   aisle. The whole aisle between the cross aisles (rows 3-10) is one-way.
5. Exact niche rows on the left wall (5, 7 for parking, 9 for candidate) were confirmed only by the
   overlay at about 1 px per 1/20 m; an off-by-one is unlikely but not excluded.
6. Rotation angle and origin are visual estimates (about +/-0.5 degree and +/-3 px).

## Check result
`amr_optimizer.layout check --robots 6`: 103 nodes (2 charger, 56 intersection, 2 parking, 3 station,
40 storage), 178 edges, "the map is fit for traffic". (The literal first version failed with 2
leaf-endpoint violations at column 11, rows 4 and 8 in that version; see decision 1.)

## Remaining differences in the last render (plan.compare.png)
- Column 11 rows 4 and 9: tan bays on the plan, `#` in the layout (deliberate, decision 1).
- Otherwise walls, rack blocks, aisles, arrows, chargers, stations, parking spots and candidates fall in
  the matching cells at the render's resolution; small sub-cell offsets (a few px) remain.
