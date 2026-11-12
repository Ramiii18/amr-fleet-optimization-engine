/**
 * Drawing the warehouse and the fleet on a 2D canvas.
 *
 * The map is drawn as what it is in the engine: a graph of points and directed connections,
 * seen from above. No aisle widths, robot footprints or clearances are drawn, because the model
 * has none. Robots are drawn at the positions the trace gives for the playhead.
 */

import { type Pose, type Track, activityAt, orderAt, poseAt } from "../lib/motion";
import { type Corridor, type Replay, conflictsAt, orderStateAt } from "../lib/replay";
import type { TraceConflict, TraceNode } from "../lib/trace";
import { type View, scaleBar, toScreen } from "../lib/view";
import { ACTIVITY_COLORS, COLORS, FONT, MONO, NODE_COLORS } from "./theme";

export interface Scene {
  nodes: readonly TraceNode[];
  nodeById: ReadonlyMap<string, TraceNode>;
  corridors: readonly Corridor[];
  /** The run being played, or null while only a map is shown. */
  replay: Replay | null;
  tick: number;
  selectedRobot: string | null;
  selectedNode: string | null;
  selectedOrder: string | null;
  hoverRobot: string | null;
  hoverNode: string | null;
  showLabels: boolean;
  showRoutes: boolean;
  showOrders: boolean;
  showConflicts: boolean;
}

/** How far the selected robot's recorded path is drawn behind and, when it is free, ahead. */
const TRAIL_SECONDS = 60;

const clamp = (value: number, low: number, high: number): number =>
  Math.min(Math.max(value, low), high);

export function nodeRadius(scale: number): number {
  return clamp(scale * 0.2, 1.4, 6.5);
}

export function robotRadius(scale: number): number {
  return clamp(scale * 0.36, 4.5, 11);
}

/** The recorded path of `track` from tick `from` to tick `to`, as map points. */
export function pathBetween(track: Track, from: number, to: number): [number, number][] {
  const start = poseAt(track, from);
  const points: [number, number][] = [[start.x, start.y]];
  for (let i = start.visit + 1; i < track.arrive.length; i++) {
    if ((track.arrive[i] as number) > to) break;
    points.push([track.xs[i] as number, track.ys[i] as number]);
  }
  const end = poseAt(track, to);
  points.push([end.x, end.y]);
  return points;
}

/** The tick at which the robot's current job ends (its next change to free), if it has one. */
function jobEnd(replay: Replay, robotId: string, tick: number): number | null {
  const changes = replay.activity.get(robotId);
  if (changes === undefined) return null;
  for (let i = 0; i < changes.ticks.length; i++) {
    if ((changes.ticks[i] as number) > tick && changes.activities[i] === "free") {
      return changes.ticks[i] as number;
    }
  }
  return null;
}

export function drawScene(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  view: View,
  scene: Scene,
): void {
  const at = (x: number, y: number): [number, number] => toScreen(view, width, height, x, y);
  const { scale } = view;
  const r = nodeRadius(scale);

  ctx.fillStyle = COLORS.background;
  ctx.fillRect(0, 0, width, height);

  drawCorridors(ctx, scene.corridors, at, scale);

  const replay = scene.replay;
  const active = replay !== null && scene.showConflicts ? conflictsAt(replay.conflicts, scene.tick) : [];
  if (replay !== null && scene.showConflicts) {
    for (const conflict of replay.conflicts) {
      if (!active.includes(conflict)) drawConflict(ctx, conflict, scene.nodeById, at, r, false);
    }
  }

  drawNodes(ctx, scene.nodes, at, r, scale);
  if (scene.showLabels) drawNodeLabels(ctx, scene.nodes, at, r, scale);

  if (replay !== null && scene.showOrders) drawWaitingLoads(ctx, replay, scene, at, r);

  const marked = (id: string | null, color: string, extra: number): void => {
    const node = id === null ? undefined : scene.nodeById.get(id);
    if (node === undefined) return;
    const [sx, sy] = at(node.x, node.y);
    ctx.beginPath();
    ctx.arc(sx, sy, r + extra, 0, Math.PI * 2);
    ctx.lineWidth = 1.75;
    ctx.strokeStyle = color;
    ctx.stroke();
  };
  marked(scene.hoverNode, COLORS.hover, 4);
  marked(scene.selectedNode, COLORS.selection, 5.5);

  if (replay !== null) {
    drawSelectedOrder(ctx, replay, scene, at, r);
    if (scene.showRoutes && scene.selectedRobot !== null) {
      drawRoute(ctx, replay, scene.selectedRobot, scene.tick, at);
    }
    drawRobots(ctx, replay, scene, at, scale);
    for (const conflict of active) drawConflict(ctx, conflict, scene.nodeById, at, r, true);
  }

  if (scene.nodes.length > 0) drawScaleBar(ctx, height, scale);
}

type At = (x: number, y: number) => [number, number];

function drawCorridors(
  ctx: CanvasRenderingContext2D,
  corridors: readonly Corridor[],
  at: At,
  scale: number,
): void {
  const lineWidth = clamp(scale * 0.09, 1, 3.2);
  ctx.lineCap = "round";
  for (const oneWay of [false, true]) {
    ctx.beginPath();
    for (const corridor of corridors) {
      if (corridor.oneWay !== oneWay) continue;
      const [ax, ay] = at(corridor.a.x, corridor.a.y);
      const [bx, by] = at(corridor.b.x, corridor.b.y);
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
    }
    ctx.lineWidth = lineWidth;
    ctx.strokeStyle = oneWay ? COLORS.corridorOneWay : COLORS.corridor;
    ctx.stroke();
  }
  // A one-way connection carries an arrowhead in its direction of travel; a two-way one has none.
  const size = clamp(scale * 0.24, 3.2, 7);
  ctx.fillStyle = COLORS.arrow;
  ctx.beginPath();
  for (const corridor of corridors) {
    if (!corridor.oneWay) continue;
    const [ax, ay] = at(corridor.a.x, corridor.a.y);
    const [bx, by] = at(corridor.b.x, corridor.b.y);
    const length = Math.hypot(bx - ax, by - ay);
    if (length < size * 3.2) continue; // too short on screen to carry a readable arrow
    const ux = (bx - ax) / length;
    const uy = (by - ay) / length;
    const mx = (ax + bx) / 2 + ux * size * 0.5;
    const my = (ay + by) / 2 + uy * size * 0.5;
    ctx.moveTo(mx, my);
    ctx.lineTo(mx - ux * size - uy * size * 0.6, my - uy * size + ux * size * 0.6);
    ctx.lineTo(mx - ux * size + uy * size * 0.6, my - uy * size - ux * size * 0.6);
    ctx.closePath();
  }
  ctx.fill();
}

function drawNodes(
  ctx: CanvasRenderingContext2D,
  nodes: readonly TraceNode[],
  at: At,
  r: number,
  scale: number,
): void {
  ctx.lineWidth = clamp(scale * 0.05, 1, 1.6);
  for (const node of nodes) {
    const [sx, sy] = at(node.x, node.y);
    const colors = NODE_COLORS[node.kind];
    ctx.fillStyle = colors.fill;
    ctx.strokeStyle = colors.stroke;
    ctx.beginPath();
    switch (node.kind) {
      case "intersection":
        ctx.arc(sx, sy, r * 0.62, 0, Math.PI * 2);
        ctx.fill();
        continue;
      case "storage":
        ctx.rect(sx - r, sy - r, r * 2, r * 2);
        break;
      case "station": {
        const d = r * 1.55;
        ctx.moveTo(sx, sy - d);
        ctx.lineTo(sx + d, sy);
        ctx.lineTo(sx, sy + d);
        ctx.lineTo(sx - d, sy);
        ctx.closePath();
        break;
      }
      case "charger":
        ctx.arc(sx, sy, r * 1.25, 0, Math.PI * 2);
        break;
      case "parking":
        ctx.roundRect(sx - r * 1.1, sy - r * 1.1, r * 2.2, r * 2.2, r * 0.7);
        break;
    }
    ctx.fill();
    ctx.stroke();
    if (r < 4.5) continue;
    if (node.kind === "charger") {
      // A lightning bolt: the usual sign for a charging place.
      ctx.beginPath();
      ctx.moveTo(sx + r * 0.2, sy - r * 0.8);
      ctx.lineTo(sx - r * 0.5, sy + r * 0.12);
      ctx.lineTo(sx - r * 0.02, sy + r * 0.12);
      ctx.lineTo(sx - r * 0.2, sy + r * 0.8);
      ctx.lineTo(sx + r * 0.5, sy - r * 0.12);
      ctx.lineTo(sx + r * 0.02, sy - r * 0.12);
      ctx.closePath();
      ctx.fillStyle = colors.stroke;
      ctx.fill();
    } else if (node.kind === "parking") {
      ctx.fillStyle = colors.stroke;
      ctx.font = `600 ${Math.round(r * 1.5)}px ${FONT}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText("P", sx, sy + 0.5);
    }
  }
}

function haloText(
  ctx: CanvasRenderingContext2D,
  textValue: string,
  x: number,
  y: number,
  color: string,
): void {
  ctx.lineWidth = 3;
  ctx.strokeStyle = COLORS.halo;
  ctx.lineJoin = "round";
  ctx.strokeText(textValue, x, y);
  ctx.fillStyle = color;
  ctx.fillText(textValue, x, y);
}

function drawNodeLabels(
  ctx: CanvasRenderingContext2D,
  nodes: readonly TraceNode[],
  at: At,
  r: number,
  scale: number,
): void {
  // Stations, chargers and parking are named first; every node is named once there is room.
  const all = scale >= 34;
  if (scale < 5.5) return;
  ctx.font = `500 ${all ? 10 : 10.5}px ${MONO}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  for (const node of nodes) {
    const minor = node.kind === "intersection" || node.kind === "storage";
    if (minor && !all) continue;
    const [sx, sy] = at(node.x, node.y);
    haloText(ctx, node.id, sx, sy + r * 1.6 + 2, minor ? COLORS.label : COLORS.labelStrong);
  }
}

/** A small count at each pickup where loads are waiting to be collected. */
function drawWaitingLoads(
  ctx: CanvasRenderingContext2D,
  replay: Replay,
  scene: Scene,
  at: At,
  r: number,
): void {
  const waiting = new Map<string, number>();
  for (const order of replay.orders) {
    const state = orderStateAt(order, scene.tick);
    if (state === "pending" || state === "to_pickup" || state === "loading") {
      waiting.set(order.pickup, (waiting.get(order.pickup) ?? 0) + 1);
    }
  }
  ctx.font = `700 9px ${MONO}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (const [nodeId, count] of waiting) {
    const node = scene.nodeById.get(nodeId);
    if (node === undefined) continue;
    const [sx, sy] = at(node.x, node.y);
    const bx = sx + r + 5;
    const by = sy - r - 5;
    ctx.beginPath();
    ctx.roundRect(bx - 6, by - 6, 12, 12, 3);
    ctx.fillStyle = COLORS.pickup;
    ctx.fill();
    ctx.fillStyle = "#1a1203";
    ctx.fillText(count > 9 ? "9+" : String(count), bx, by + 0.5);
  }
}

function drawSelectedOrder(
  ctx: CanvasRenderingContext2D,
  replay: Replay,
  scene: Scene,
  at: At,
  r: number,
): void {
  // The selected order, or else the order the selected robot is working on.
  let orderId = scene.selectedOrder;
  if (orderId === null && scene.selectedRobot !== null) {
    orderId = orderAt(replay.activity.get(scene.selectedRobot), scene.tick);
  }
  const order = orderId === null ? undefined : replay.orderById.get(orderId);
  if (order === undefined) return;
  const ends: [string, string, string][] = [
    [order.pickup, COLORS.pickup, "PICKUP"],
    [order.dropoff, COLORS.dropoff, "DROP-OFF"],
  ];
  ctx.font = `700 9.5px ${FONT}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "bottom";
  for (const [nodeId, color, label] of ends) {
    const node = scene.nodeById.get(nodeId);
    if (node === undefined) continue;
    const [sx, sy] = at(node.x, node.y);
    ctx.beginPath();
    ctx.arc(sx, sy, r + 7, 0, Math.PI * 2);
    ctx.lineWidth = 2;
    ctx.strokeStyle = color;
    ctx.setLineDash([4, 3]);
    ctx.stroke();
    ctx.setLineDash([]);
    haloText(ctx, label, sx, sy - r - 10, color);
  }
}

function drawRoute(ctx: CanvasRenderingContext2D, replay: Replay, robotId: string, tick: number, at: At): void {
  const track = replay.trackById.get(robotId);
  if (track === undefined) return;
  const span = TRAIL_SECONDS / replay.tickS;
  const stroke = (points: [number, number][]): void => {
    ctx.beginPath();
    points.forEach(([x, y], i) => {
      const [sx, sy] = at(x, y);
      if (i === 0) ctx.moveTo(sx, sy);
      else ctx.lineTo(sx, sy);
    });
    ctx.stroke();
  };
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  // Behind: where it has been in the last minute.
  ctx.lineWidth = 3;
  ctx.strokeStyle = COLORS.trailPast;
  stroke(pathBetween(track, Math.max(0, tick - span), tick));
  // Ahead: the recorded path to the end of its current job, or the next minute while it is free.
  const until = Math.min(jobEnd(replay, robotId, tick) ?? tick + span, replay.endTick);
  if (until > tick) {
    ctx.lineWidth = 2;
    ctx.strokeStyle = COLORS.trailAhead;
    ctx.setLineDash([6, 5]);
    stroke(pathBetween(track, tick, until));
    ctx.setLineDash([]);
  }
}

function heading(track: Track, pose: Pose): [number, number] | null {
  if (!pose.moving) return null;
  const dx = (track.xs[pose.visit + 1] as number) - (track.xs[pose.visit] as number);
  const dy = (track.ys[pose.visit + 1] as number) - (track.ys[pose.visit] as number);
  const length = Math.hypot(dx, dy);
  return length === 0 ? null : [dx / length, -dy / length]; // screen y points down
}

function drawRobots(
  ctx: CanvasRenderingContext2D,
  replay: Replay,
  scene: Scene,
  at: At,
  scale: number,
): void {
  const radius = robotRadius(scale);
  const labelled = scale >= 15 || replay.tracks.length <= 12;
  // The selected robot is drawn last, on top.
  const order = [...replay.tracks].sort(
    (a, b) => Number(a.id === scene.selectedRobot) - Number(b.id === scene.selectedRobot),
  );
  for (const track of order) {
    const pose = poseAt(track, scene.tick);
    const [sx, sy] = at(pose.x, pose.y);
    const activity = activityAt(replay.activity.get(track.id), scene.tick);
    const selected = track.id === scene.selectedRobot;
    const hovered = track.id === scene.hoverRobot;

    if (selected || hovered) {
      ctx.beginPath();
      ctx.arc(sx, sy, radius + 5, 0, Math.PI * 2);
      ctx.lineWidth = selected ? 2.25 : 1.5;
      ctx.strokeStyle = selected ? COLORS.selection : COLORS.hover;
      ctx.stroke();
    }

    const direction = heading(track, pose);
    if (direction !== null) {
      // A pointer on the side the robot is driving towards.
      const [ux, uy] = direction;
      ctx.beginPath();
      ctx.moveTo(sx + ux * (radius + 5), sy + uy * (radius + 5));
      ctx.lineTo(sx + ux * radius * 0.55 - uy * radius * 0.8, sy + uy * radius * 0.55 + ux * radius * 0.8);
      ctx.lineTo(sx + ux * radius * 0.55 + uy * radius * 0.8, sy + uy * radius * 0.55 - ux * radius * 0.8);
      ctx.closePath();
      ctx.fillStyle = ACTIVITY_COLORS[activity];
      ctx.fill();
    }

    ctx.beginPath();
    ctx.arc(sx, sy, radius, 0, Math.PI * 2);
    ctx.fillStyle = ACTIVITY_COLORS[activity];
    ctx.fill();
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = COLORS.background;
    ctx.stroke();

    // The activity is also a shape, not only a colour: a load is a filled square, a robot on
    // its way to a pickup carries an empty one, and a free robot carries nothing.
    const box = radius * 0.5;
    if (activity === "loaded") {
      ctx.fillStyle = COLORS.background;
      ctx.fillRect(sx - box, sy - box, box * 2, box * 2);
    } else if (activity === "to pickup") {
      ctx.lineWidth = 1.4;
      ctx.strokeStyle = COLORS.background;
      ctx.strokeRect(sx - box, sy - box, box * 2, box * 2);
    }

    if (labelled || selected || hovered) {
      ctx.font = `600 ${selected ? 11 : 10}px ${MONO}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      haloText(ctx, track.id, sx, sy - radius - 6, selected ? COLORS.selection : COLORS.labelStrong);
    }
  }
}

function drawConflict(
  ctx: CanvasRenderingContext2D,
  conflict: TraceConflict,
  nodeById: ReadonlyMap<string, TraceNode>,
  at: At,
  r: number,
  active: boolean,
): void {
  const points = conflict.resource
    .map((id) => nodeById.get(id))
    .filter((node): node is TraceNode => node !== undefined)
    .map((node) => at(node.x, node.y));
  const [first, second] = points;
  if (first === undefined) return;
  ctx.strokeStyle = COLORS.conflict;
  ctx.globalAlpha = active ? 1 : 0.3;
  if (second === undefined) {
    // A conflict at a node: a ring around it (two rings while it is happening).
    ctx.lineWidth = active ? 2.5 : 1.5;
    ctx.beginPath();
    ctx.arc(first[0], first[1], r + (active ? 12 : 8), 0, Math.PI * 2);
    ctx.stroke();
    if (active) {
      ctx.beginPath();
      ctx.arc(first[0], first[1], r + 17, 0, Math.PI * 2);
      ctx.lineWidth = 1;
      ctx.stroke();
    }
  } else {
    // A conflict in a corridor: the corridor itself, marked along its length.
    ctx.lineWidth = active ? 6 : 3.5;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(first[0], first[1]);
    ctx.lineTo(second[0], second[1]);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
}

function drawScaleBar(ctx: CanvasRenderingContext2D, height: number, scale: number): void {
  const bar = scaleBar(scale);
  const x = 18;
  const y = height - 22;
  ctx.strokeStyle = COLORS.label;
  ctx.lineWidth = 1.5;
  ctx.lineCap = "butt";
  ctx.beginPath();
  ctx.moveTo(x, y - 4);
  ctx.lineTo(x, y);
  ctx.lineTo(x + bar.px, y);
  ctx.lineTo(x + bar.px, y - 4);
  ctx.stroke();
  ctx.font = `500 10.5px ${MONO}`;
  ctx.textAlign = "left";
  ctx.textBaseline = "bottom";
  haloText(ctx, `${bar.metres} m`, x + 5, y - 3, COLORS.label);
}

/** What is under the screen point (sx, sy): a robot first, then a node. */
export function pickAt(
  scene: Scene,
  view: View,
  width: number,
  height: number,
  sx: number,
  sy: number,
): { robot: string | null; node: string | null } {
  let robot: string | null = null;
  if (scene.replay !== null) {
    let best = (robotRadius(view.scale) + 6) ** 2;
    for (const track of scene.replay.tracks) {
      const pose = poseAt(track, scene.tick);
      const [x, y] = toScreen(view, width, height, pose.x, pose.y);
      const distance = (x - sx) ** 2 + (y - sy) ** 2;
      if (distance <= best) {
        best = distance;
        robot = track.id;
      }
    }
  }
  if (robot !== null) return { robot, node: null };
  let node: string | null = null;
  let best = (nodeRadius(view.scale) + 7) ** 2;
  for (const candidate of scene.nodes) {
    const [x, y] = toScreen(view, width, height, candidate.x, candidate.y);
    const distance = (x - sx) ** 2 + (y - sy) ** 2;
    if (distance <= best) {
      best = distance;
      node = candidate.id;
    }
  }
  return { robot: null, node };
}
