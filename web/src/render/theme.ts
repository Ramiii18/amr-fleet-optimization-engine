/**
 * Colours of the map canvas. They repeat the CSS variables in `styles.css`, because a canvas
 * cannot read a stylesheet cheaply on every frame.
 *
 * Colour never stands alone: every activity and node kind also has its own shape or label, and
 * the legend names each one.
 */

import type { Activity } from "../lib/motion";
import type { NodeKind } from "../lib/trace";

export const COLORS = {
  background: "#0a0e13",
  grid: "#10161e",
  corridor: "#243242",
  corridorOneWay: "#2d4157",
  arrow: "#4a6583",
  label: "#8fa2b7",
  labelStrong: "#e6edf5",
  halo: "#0a0e13",
  selection: "#38bdf8",
  hover: "#cbd5e1",
  conflict: "#f87171",
  pickup: "#f5b041",
  dropoff: "#34d399",
  trailPast: "rgba(56, 189, 248, 0.38)",
  trailAhead: "#38bdf8",
} as const;

export const NODE_COLORS: Record<NodeKind, { fill: string; stroke: string }> = {
  intersection: { fill: "#33455a", stroke: "#33455a" },
  storage: { fill: "#152030", stroke: "#4f6a88" },
  station: { fill: "#8b7cf6", stroke: "#c4b8ff" },
  charger: { fill: "#3a3210", stroke: "#eab308" },
  parking: { fill: "#131b25", stroke: "#64748b" },
};

export const NODE_LABEL: Record<NodeKind, string> = {
  intersection: "Intersection",
  storage: "Storage",
  station: "Station",
  charger: "Charger",
  parking: "Parking",
};

export const ACTIVITY_COLORS: Record<Activity, string> = {
  free: "#94a3b8",
  "to pickup": "#f5b041",
  loaded: "#34d399",
};

export const ACTIVITY_LABEL: Record<Activity, string> = {
  free: "Free",
  "to pickup": "To pickup",
  loaded: "Loaded",
};

export const FONT = '"Inter", "Segoe UI", system-ui, -apple-system, sans-serif';
export const MONO = '"JetBrains Mono", "Cascadia Mono", "Consolas", ui-monospace, monospace';
