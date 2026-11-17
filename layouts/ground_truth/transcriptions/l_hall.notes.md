# plan.layout.json: transcription notes

Synthetic plan ("invented for testing, not a real site"). This is an approximation built from the image, not an exact copy.

## Scale and origin (measured, not guessed)
- Scale bar "0 ... metres ... 5": its dark pixels run from x=528 to x=648 on rows y=668..674. That is 120 px for 5 m, so **24 px/m**.
- Building outline (wall grey RGB 75,79,84, searched only inside the plan area): x=48..647, y=48..575. **Origin pixel (48, 48)**. The size is 600 x 528 px, which is **25.0 m x 22.0 m**: a whole number of metres both ways.
- Orientation: no north arrow. Row 0 is the top of the image.

## Cell size
**1.0 m (24 px).** It is the default, and every feature on the plan lies on a 24 px pitch from the origin: rack bays, aisles, wall niches, wall stubs and symbols. The cell grid is 25 columns x 22 rows. I sampled every cell's colour with a pixel script and found no feature that falls between cells. Every aisle is exactly one cell wide, and every rack bay is exactly one cell.

## What I saw
- Outline: an L/T-shaped drivable area inside a 25 x 22 m rectangle. The top-right block (x 312..648, y 48..288 in pixels) is solid wall or no access and is `#`.
- Upper rack block, rows 3-9: aisles in columns 1, 4, 7 and 10. Double-deep racks in columns 2-3, 5-6 and 8-9, five bays each (rows 4-8). One single-deep rack in column 11, which only faces aisle 10. Rack ends (rows 3 and 9) are dark grey and are `#`.
- Lower rack block, rows 12-18: aisles in columns 1, 4, 7, 10, 13, 16, 19 and 22. Double-deep racks in columns 2-3 through 20-21, five bays each (rows 13-17). One single-deep rack in column 23, which faces aisle 22. Rack ends (rows 12 and 18) are `#`.
- Cross aisles, two-way and without arrows: row 2 (top), rows 10-11 (middle, two rows), row 19 (bottom).
- Wall stubs in middle row 10 at columns 3, 9, 15 and 21: 1 x 1 m blocks in wall grey. The legend's "Column" colour is black, and the plan has no black blocks, so these are probably wall stubs, not columns. Either way they are `#`.
- One-way arrows (painted in rows 3, 6, 9 above and rows 12, 15, 18 below). Columns 1, 7, 13 and 19 point up (`^`); columns 4, 10, 16 and 22 point down (`v`). Following the convention, the whole aisle between the two cross aisles is one-way (rows 3-9 and 12-18), not only the cells with a painted arrow.
- Symbols, read from the legend; each one sits in a one-cell niche of a wall:
  - Chargers (green circle): (row 1, col 2) and (row 1, col 10).
  - Candidate positions (dashed square): (1,5), (1,8), (7,0), (20,5), (20,8), (20,11), (20,17), (20,20).
  - Parking spots (orange square): (5,0), (14,0), (16,0).
  - Pick/drop stations (blue square): (20,2) and (20,14).
- No doors or docks are drawn.

## Uncertainties and decisions
1. **Endpoints in the outer wall's column or row.** The parking spots and the candidate at (7,0) lie in column 0, and the chargers, stations and candidates in rows 1 and 20 sit in niches cut into the outer wall band. So column 0 is not a solid line of `#`. I followed "a symbol in a niche of a wall is an endpoint in that niche's cell".
2. **Middle wall-grey blocks (row 10).** I treated them as `#`, whether they are columns or wall stubs. I am fairly sure they are not drivable.
3. **Extent of the one-way aisles.** I made the whole aisle one-way between its cross aisles, including the rows that hold the rack-end `#` and have a painted arrow (3, 9, 12, 18). The cross-aisle cells at the ends of each aisle (rows 2, 10, 11 and 19) are two-way. Another reader could have extended the arrows into those cells; I did not.
4. **Single-deep racks at columns 11 and 23.** Each is one bay deep and faces only the aisle on its left. Its right side is wall or no-access space. I read nothing behind them.
5. **Candidate positions (`?`)** become nodes only once they are assigned. The compiled map leaves them out, as expected.
6. **Robot clearance.** I assumed that a 1 m aisle carries one robot per cell. The plan gives no robot size.

## Check result
`check --robots 6`: 273 nodes (2 charger, 156 intersection, 3 parking, 2 station, 110 storage), 494 edges. "the map is fit for traffic".

## Render
One render (`plan.compare.png`) with px-per-m 24 and origin (48, 48). The cell grid lies on the plan's edges. Racks, aisles, arrows, stubs and every symbol fall in the cells I assigned. At the render's resolution I saw no mismatch, so I did not render again.
