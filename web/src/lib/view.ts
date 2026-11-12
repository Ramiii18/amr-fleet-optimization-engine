/**
 * The map's view: which part of the warehouse is on screen, and at what scale.
 *
 * Map coordinates are metres, x to the right and y up, as in the engine. Screen coordinates are
 * CSS pixels, y down. One scale applies to both axes, so distances and angles are never distorted.
 */

import type { Bounds } from "./replay";

export interface View {
  /** Pixels per metre. */
  scale: number;
  /** The map point at the centre of the screen. */
  cx: number;
  cy: number;
}

export const MIN_SCALE = 1.5;
export const MAX_SCALE = 160;

export function toScreen(view: View, width: number, height: number, x: number, y: number): [number, number] {
  return [width / 2 + (x - view.cx) * view.scale, height / 2 - (y - view.cy) * view.scale];
}

export function toWorld(view: View, width: number, height: number, sx: number, sy: number): [number, number] {
  return [view.cx + (sx - width / 2) / view.scale, view.cy - (sy - height / 2) / view.scale];
}

/** The view that shows all of `bounds`, with `padding` pixels free on every side. */
export function fitView(bounds: Bounds, width: number, height: number, padding = 64): View {
  const spanX = Math.max(bounds.maxX - bounds.minX, 1e-6);
  const spanY = Math.max(bounds.maxY - bounds.minY, 1e-6);
  const room = (size: number): number => Math.max(size - 2 * padding, 40);
  const scale = Math.min(room(width) / spanX, room(height) / spanY);
  return {
    scale: Math.min(Math.max(scale, MIN_SCALE), MAX_SCALE),
    cx: (bounds.minX + bounds.maxX) / 2,
    cy: (bounds.minY + bounds.maxY) / 2,
  };
}

/** Zoom by `factor`, keeping the map point under the screen point (sx, sy) where it is. */
export function zoomAt(
  view: View,
  width: number,
  height: number,
  sx: number,
  sy: number,
  factor: number,
): View {
  const scale = Math.min(Math.max(view.scale * factor, MIN_SCALE), MAX_SCALE);
  const [x, y] = toWorld(view, width, height, sx, sy);
  return {
    scale,
    cx: x - (sx - width / 2) / scale,
    cy: y + (sy - height / 2) / scale,
  };
}

/** Move the view by a drag of (dx, dy) screen pixels. */
export function pan(view: View, dx: number, dy: number): View {
  return { ...view, cx: view.cx - dx / view.scale, cy: view.cy + dy / view.scale };
}

/** A round length for a scale bar about `targetPx` wide: 1, 2 or 5 times a power of ten metres. */
export function scaleBar(scale: number, targetPx = 110): { metres: number; px: number } {
  const raw = targetPx / scale;
  const power = 10 ** Math.floor(Math.log10(raw));
  const lead = raw / power;
  const metres = (lead >= 5 ? 5 : lead >= 2 ? 2 : 1) * power;
  return { metres, px: metres * scale };
}
