import { describe, expect, it } from "vitest";

import { pathBetween } from "../render/scene";
import { traceJson } from "./fixtures.test-util";
import { positionAt } from "./motion";
import { buildReplay } from "./replay";
import { parseTrace } from "./trace";
import { MAX_SCALE, MIN_SCALE, fitView, pan, scaleBar, toScreen, toWorld, zoomAt } from "./view";

const BOUNDS = { minX: 0, minY: 0, maxX: 46, maxY: 40 };
const [W, H] = [1000, 700];

describe("the map view", () => {
  it("fits the whole map inside the padding, at one scale for both axes", () => {
    const view = fitView(BOUNDS, W, H, 50);
    const [left, bottom] = toScreen(view, W, H, BOUNDS.minX, BOUNDS.minY);
    const [right, top] = toScreen(view, W, H, BOUNDS.maxX, BOUNDS.maxY);
    for (const [value, low, high] of [
      [left, 50, W - 50],
      [right, 50, W - 50],
      [top, 50, H - 50],
      [bottom, 50, H - 50],
    ] as const) {
      expect(value).toBeGreaterThanOrEqual(low - 1e-9);
      expect(value).toBeLessThanOrEqual(high + 1e-9);
    }
    // The tighter axis touches the padding, and y grows upwards on the map.
    expect(Math.min(left - 50, top - 50)).toBeCloseTo(0, 6);
    expect(bottom).toBeGreaterThan(top);
    // One metre is as many pixels across as it is up.
    const [x1, y1] = toScreen(view, W, H, 10, 10);
    const [x2, y2] = toScreen(view, W, H, 11, 11);
    expect(x2 - x1).toBeCloseTo(y1 - y2, 9);
  });

  it("copes with a map of one point, and with a tiny canvas", () => {
    const point = fitView({ minX: 5, minY: 5, maxX: 5, maxY: 5 }, W, H);
    expect(point.scale).toBe(MAX_SCALE);
    expect(toScreen(point, W, H, 5, 5)).toEqual([W / 2, H / 2]);
    expect(fitView(BOUNDS, 10, 10).scale).toBeGreaterThanOrEqual(MIN_SCALE);
  });

  it("converts between the map and the screen and back", () => {
    const view = { scale: 17.3, cx: 12.5, cy: -4 };
    const [sx, sy] = toScreen(view, W, H, 30.25, 7.5);
    const [x, y] = toWorld(view, W, H, sx, sy);
    expect(x).toBeCloseTo(30.25, 9);
    expect(y).toBeCloseTo(7.5, 9);
  });

  it("zooms about the pointer, and stays within its limits", () => {
    const view = fitView(BOUNDS, W, H);
    const before = toWorld(view, W, H, 200, 150);
    const zoomed = zoomAt(view, W, H, 200, 150, 2.5);
    const after = toWorld(zoomed, W, H, 200, 150);
    expect(zoomed.scale).toBeCloseTo(view.scale * 2.5, 9);
    expect(after[0]).toBeCloseTo(before[0], 9);
    expect(after[1]).toBeCloseTo(before[1], 9);
    expect(zoomAt(view, W, H, 0, 0, 1e9).scale).toBe(MAX_SCALE);
    expect(zoomAt(view, W, H, 0, 0, 1e-9).scale).toBe(MIN_SCALE);
  });

  it("pans by the distance of the drag", () => {
    const view = { scale: 10, cx: 0, cy: 0 };
    const moved = pan(view, 30, -20);
    expect(toScreen(moved, W, H, 0, 0)).toEqual([W / 2 + 30, H / 2 - 20]);
  });

  it("draws a scale bar of a round length", () => {
    expect(scaleBar(17.6)).toEqual({ metres: 5, px: 88 });
    expect(scaleBar(110).metres).toBe(1);
    expect(scaleBar(2).metres).toBe(50);
    for (const scale of [1.5, 4, 9, 33, 160]) {
      const bar = scaleBar(scale);
      expect(bar.px).toBeGreaterThan(40);
      expect(bar.px).toBeLessThanOrEqual(110 + 1e-9);
    }
  });
});

describe("pathBetween", () => {
  const replay = buildReplay(parseTrace(traceJson("disturbed")));

  it("starts and ends at the robot's positions, through the recorded nodes in between", () => {
    for (const track of replay.tracks) {
      const [from, to] = [replay.endTick * 0.31, replay.endTick * 0.47];
      const points = pathBetween(track, from, to);
      expect(points[0]).toEqual(positionAt(track, from));
      expect(points.at(-1)).toEqual(positionAt(track, to));
      const inside = [...track.arrive].filter((arrive) => arrive > from && arrive <= to).length;
      expect(points.length).toBe(inside + 2);
    }
  });

  it("is a single place when no time passes", () => {
    const track = replay.tracks[0];
    if (track === undefined) throw new Error("no robot");
    const here = positionAt(track, 1234.5);
    expect(pathBetween(track, 1234.5, 1234.5)).toEqual([here, here]);
  });
});
