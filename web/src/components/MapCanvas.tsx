import { type Ref, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";

import { activityAt, poseAt } from "../lib/motion";
import type { Playback } from "../lib/playback";
import type { Bounds, Corridor, Replay } from "../lib/replay";
import type { TraceNode } from "../lib/trace";
import { type View, fitView, pan, toScreen, zoomAt } from "../lib/view";
import { type Scene, drawScene, pickAt } from "../render/scene";
import {
  ACTIVITY_COLORS,
  ACTIVITY_LABEL,
  NODE_COLORS,
  NODE_LABEL,
  STATUS_COLORS,
  STATUS_SHORT,
  STATUS_SIGN,
} from "../render/theme";

export interface Selection {
  robot: string | null;
  node: string | null;
  order: string | null;
}

export interface MapHandle {
  fit: () => void;
  zoom: (factor: number) => void;
}

interface Props {
  ref?: Ref<MapHandle>;
  nodes: readonly TraceNode[];
  nodeById: ReadonlyMap<string, TraceNode>;
  corridors: readonly Corridor[];
  bounds: Bounds;
  replay: Replay | null;
  playback: Playback | null;
  selection: Selection;
  onSelect: (selection: Selection) => void;
  /** A short text over the map while there is nothing to draw. */
  placeholder?: string | null;
}

/** Height in pixels kept free at the bottom of the canvas for the legend. */
const LEGEND_ROOM = 44;

interface Tip {
  x: number;
  y: number;
  title: string;
  detail: string;
}

interface Options {
  labels: boolean;
  routes: boolean;
  orders: boolean;
  conflicts: boolean;
  status: boolean;
  follow: boolean;
}

const TOGGLES: { key: keyof Options; label: string; title: string; needsRun: boolean }[] = [
  { key: "labels", label: "Labels", title: "Node names", needsRun: false },
  { key: "routes", label: "Route", title: "The selected robot's recorded path", needsRun: true },
  {
    key: "status",
    label: "Status",
    title: "Charging, out of service, breakdowns, delays and waits, from the run's robot events",
    needsRun: true,
  },
  { key: "orders", label: "Loads", title: "Loads waiting at their pickup", needsRun: true },
  { key: "conflicts", label: "Conflicts", title: "Where the conflict checker found conflicts", needsRun: true },
  { key: "follow", label: "Follow", title: "Keep the selected robot in the centre", needsRun: true },
];

export function MapCanvas({
  ref,
  nodes,
  nodeById,
  corridors,
  bounds,
  replay,
  playback,
  selection,
  onSelect,
  placeholder,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewRef = useRef<View>({ scale: 10, cx: 0, cy: 0 });
  const sizeRef = useRef({ width: 0, height: 0 });
  const dirtyRef = useRef(true);
  const hoverRef = useRef<{ robot: string | null; node: string | null }>({ robot: null, node: null });
  const fittedRef = useRef<Bounds | null>(null);
  /** True once the user has panned or zoomed since the last fit: their view is then kept. */
  const movedRef = useRef(false);
  const [options, setOptions] = useState<Options>({
    labels: true,
    routes: true,
    orders: true,
    conflicts: true,
    status: true,
    follow: false,
  });
  const [tip, setTip] = useState<Tip | null>(null);
  const [grabbing, setGrabbing] = useState(false);

  const fit = useCallback(() => {
    const { width, height } = sizeRef.current;
    if (width === 0 || height === 0) return;
    // The legend covers the bottom of the canvas, so the map is fitted into the part above it.
    const view = fitView(bounds, width, height - LEGEND_ROOM);
    viewRef.current = { ...view, cy: view.cy - LEGEND_ROOM / 2 / view.scale };
    fittedRef.current = bounds;
    movedRef.current = false;
    dirtyRef.current = true;
  }, [bounds]);

  const zoom = useCallback((factor: number) => {
    const { width, height } = sizeRef.current;
    viewRef.current = zoomAt(viewRef.current, width, height, width / 2, height / 2, factor);
    movedRef.current = true;
    dirtyRef.current = true;
  }, []);

  useImperativeHandle(ref, () => ({ fit, zoom }), [fit, zoom]);

  // The newest props, for the drawing loop and the pointer handlers, which outlive a render.
  const latest = useRef({ nodes, nodeById, corridors, replay, playback, selection, options, onSelect });
  useEffect(() => {
    latest.current = { nodes, nodeById, corridors, replay, playback, selection, options, onSelect };
    dirtyRef.current = true;
  });

  // A new map is shown whole.
  useEffect(() => {
    if (fittedRef.current !== bounds) fit();
  }, [bounds, fit]);

  const sceneNow = useCallback((): Scene => {
    const now = latest.current;
    return {
      nodes: now.nodes,
      nodeById: now.nodeById,
      corridors: now.corridors,
      replay: now.replay,
      tick: now.playback?.tick ?? 0,
      selectedRobot: now.selection.robot,
      selectedNode: now.selection.node,
      selectedOrder: now.selection.order,
      hoverRobot: hoverRef.current.robot,
      hoverNode: hoverRef.current.node,
      showLabels: now.options.labels,
      showRoutes: now.options.routes,
      showOrders: now.options.orders,
      showConflicts: now.options.conflicts,
      showStatus: now.options.status,
    };
  }, []);

  // Size: the canvas follows its container, at the screen's pixel density.
  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    if (wrap === null || canvas === null) return;
    const resize = (): void => {
      const { width, height } = wrap.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      sizeRef.current = { width, height };
      canvas.width = Math.max(1, Math.round(width * ratio));
      canvas.height = Math.max(1, Math.round(height * ratio));
      // A view the user has not touched keeps showing the whole map when the window changes.
      if (!movedRef.current) fit();
      dirtyRef.current = true;
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(wrap);
    return () => observer.disconnect();
  }, [fit]);

  // Drawing: one loop, which draws only when something changed.
  useEffect(() => {
    let frame = 0;
    let drawnVersion = -1;
    const loop = (): void => {
      frame = requestAnimationFrame(loop);
      const canvas = canvasRef.current;
      const now = latest.current;
      const version = now.playback?.version ?? 0;
      if (canvas === null || (!dirtyRef.current && version === drawnVersion)) return;
      const { width, height } = sizeRef.current;
      if (width === 0 || height === 0) return;
      const ctx = canvas.getContext("2d");
      if (ctx === null) return;
      const scene = sceneNow();
      if (now.options.follow && scene.replay !== null && scene.selectedRobot !== null) {
        const track = scene.replay.trackById.get(scene.selectedRobot);
        if (track !== undefined) {
          const pose = poseAt(track, scene.tick);
          viewRef.current = { ...viewRef.current, cx: pose.x, cy: pose.y };
        }
      }
      const ratio = canvas.width / width;
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      drawScene(ctx, width, height, viewRef.current, scene);
      dirtyRef.current = false;
      drawnVersion = version;
    };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, [sceneNow]);

  // Pointer: drag to pan, pinch or wheel to zoom, click to select, hover to inspect.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null) return;
    const pointers = new Map<number, { x: number; y: number }>();
    let moved = 0;
    let pinch = 0;

    const local = (event: PointerEvent | WheelEvent): [number, number] => {
      const box = canvas.getBoundingClientRect();
      return [event.clientX - box.left, event.clientY - box.top];
    };
    const hover = (sx: number, sy: number): void => {
      const { width, height } = sizeRef.current;
      const scene = sceneNow();
      const hit = pickAt(scene, viewRef.current, width, height, sx, sy);
      if (hit.robot !== hoverRef.current.robot || hit.node !== hoverRef.current.node) {
        hoverRef.current = hit;
        dirtyRef.current = true;
      }
      if (hit.robot !== null && scene.replay !== null) {
        const activity = activityAt(scene.replay.activity.get(hit.robot), scene.tick);
        setTip({ x: sx, y: sy, title: hit.robot, detail: `Robot · ${ACTIVITY_LABEL[activity]}` });
      } else if (hit.node !== null) {
        const node = scene.nodeById.get(hit.node);
        setTip(
          node === undefined
            ? null
            : {
                x: sx,
                y: sy,
                title: node.id,
                detail: `${NODE_LABEL[node.kind]} · x ${node.x.toFixed(1)} m, y ${node.y.toFixed(1)} m`,
              },
        );
      } else {
        setTip(null);
      }
    };

    const down = (event: PointerEvent): void => {
      canvas.setPointerCapture(event.pointerId);
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      moved = 0;
      pinch = 0;
      setGrabbing(true);
      setTip(null);
    };
    const move = (event: PointerEvent): void => {
      const previous = pointers.get(event.pointerId);
      if (previous === undefined) {
        const [sx, sy] = local(event);
        hover(sx, sy);
        return;
      }
      const dx = event.clientX - previous.x;
      const dy = event.clientY - previous.y;
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      moved += Math.abs(dx) + Math.abs(dy);
      const { width, height } = sizeRef.current;
      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        if (a === undefined || b === undefined) return;
        const distance = Math.hypot(a.x - b.x, a.y - b.y);
        if (pinch > 0 && distance > 0) {
          movedRef.current = true;
          const box = canvas.getBoundingClientRect();
          viewRef.current = zoomAt(
            viewRef.current,
            width,
            height,
            (a.x + b.x) / 2 - box.left,
            (a.y + b.y) / 2 - box.top,
            distance / pinch,
          );
        }
        pinch = distance;
      } else if (moved > 4) {
        movedRef.current = true;
        viewRef.current = pan(viewRef.current, dx, dy);
        if (latest.current.options.follow) setOptions((o) => ({ ...o, follow: false }));
      }
      dirtyRef.current = true;
    };
    const up = (event: PointerEvent): void => {
      const wasDown = pointers.delete(event.pointerId);
      if (pointers.size === 0) setGrabbing(false);
      pinch = 0;
      if (!wasDown || moved > 4 || pointers.size > 0) return;
      const [sx, sy] = local(event);
      const { width, height } = sizeRef.current;
      const hit = pickAt(sceneNow(), viewRef.current, width, height, sx, sy);
      const now = latest.current;
      if (hit.robot !== null) now.onSelect({ robot: hit.robot, node: null, order: null });
      else if (hit.node !== null) now.onSelect({ ...now.selection, node: hit.node });
      else now.onSelect({ robot: null, node: null, order: null });
    };
    const leave = (): void => {
      if (hoverRef.current.robot !== null || hoverRef.current.node !== null) {
        hoverRef.current = { robot: null, node: null };
        dirtyRef.current = true;
      }
      setTip(null);
    };
    const wheel = (event: WheelEvent): void => {
      event.preventDefault();
      const [sx, sy] = local(event);
      const { width, height } = sizeRef.current;
      const step = event.deltaMode === 1 ? event.deltaY * 32 : event.deltaY;
      viewRef.current = zoomAt(viewRef.current, width, height, sx, sy, Math.exp(-step * 0.0016));
      movedRef.current = true;
      dirtyRef.current = true;
      setTip(null);
    };

    canvas.addEventListener("pointerdown", down);
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", up);
    canvas.addEventListener("pointercancel", up);
    canvas.addEventListener("pointerleave", leave);
    canvas.addEventListener("wheel", wheel, { passive: false });
    canvas.addEventListener("dblclick", fit);
    return () => {
      canvas.removeEventListener("pointerdown", down);
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerup", up);
      canvas.removeEventListener("pointercancel", up);
      canvas.removeEventListener("pointerleave", leave);
      canvas.removeEventListener("wheel", wheel);
      canvas.removeEventListener("dblclick", fit);
    };
  }, [fit, sceneNow]);

  // Selecting a robot in a list brings it into view if it is off screen.
  useEffect(() => {
    if (replay === null || playback === null || selection.robot === null) return;
    const track = replay.trackById.get(selection.robot);
    if (track === undefined) return;
    const { width, height } = sizeRef.current;
    const pose = poseAt(track, playback.tick);
    const [sx, sy] = toScreen(viewRef.current, width, height, pose.x, pose.y);
    if (sx < 30 || sy < 30 || sx > width - 30 || sy > height - 30) {
      viewRef.current = { ...viewRef.current, cx: pose.x, cy: pose.y };
      dirtyRef.current = true;
    }
  }, [replay, playback, selection.robot]);

  const hasRun = replay !== null;
  const kinds = [...new Set(nodes.map((node) => node.kind))];
  const oneWay = corridors.some((corridor) => corridor.oneWay);

  return (
    <div className="map" ref={wrapRef}>
      <canvas
        ref={canvasRef}
        className={grabbing ? "map-canvas grabbing" : "map-canvas"}
        role="img"
        aria-label={
          hasRun
            ? "Top-down view of the warehouse graph with the robots of the run at the playhead. The Robots tab lists the same robots as text."
            : "Top-down view of the warehouse graph: nodes and directed connections."
        }
      />

      <div className="map-toolbar" role="toolbar" aria-label="Map view">
        <div className="button-group">
          <button type="button" onClick={() => zoom(1 / 1.35)} title="Zoom out (−)" aria-label="Zoom out">
            −
          </button>
          <button type="button" onClick={() => zoom(1.35)} title="Zoom in (+)" aria-label="Zoom in">
            +
          </button>
          <button type="button" onClick={fit} title="Fit the whole map (F)">
            Fit
          </button>
        </div>
        <div className="button-group">
          {TOGGLES.filter((toggle) => hasRun || !toggle.needsRun).map((toggle) => (
            <button
              key={toggle.key}
              type="button"
              className={options[toggle.key] ? "on" : ""}
              aria-pressed={options[toggle.key]}
              title={toggle.title}
              onClick={() => setOptions((o) => ({ ...o, [toggle.key]: !o[toggle.key] }))}
            >
              {toggle.label}
            </button>
          ))}
        </div>
      </div>

      <div className="map-legend" aria-label="Legend" hidden={nodes.length === 0}>
        {hasRun && (
          <div className="legend-row">
            {(["free", "to pickup", "loaded"] as const).map((activity) => (
              <span key={activity} className="legend-item">
                <span className={`robot-dot ${activity.replace(" ", "-")}`} style={{ background: ACTIVITY_COLORS[activity] }} />
                {ACTIVITY_LABEL[activity]}
              </span>
            ))}
          </div>
        )}
        {replay !== null && replay.observed && options.status && (
          <div className="legend-row">
            {(["charging", "out_of_service", "breakdown", "delay", "corridor_wait"] as const).map((status) => (
              <span key={status} className="legend-item">
                <span className={`status-mark ${status}`} style={{ borderColor: STATUS_COLORS[status], color: STATUS_COLORS[status] }}>
                  {STATUS_SIGN[status]}
                </span>
                {STATUS_SHORT[status]}
              </span>
            ))}
          </div>
        )}
        <div className="legend-row">
          {kinds.map((kind) => (
            <span key={kind} className="legend-item">
              <span
                className={`node-mark ${kind}`}
                style={{ background: NODE_COLORS[kind].fill, borderColor: NODE_COLORS[kind].stroke }}
              />
              {NODE_LABEL[kind]}
            </span>
          ))}
          {oneWay && (
            <span className="legend-item">
              <span className="arrow-mark" aria-hidden="true">
                →
              </span>
              One-way
            </span>
          )}
        </div>
      </div>

      {tip !== null && (
        <div className="map-tip" style={{ left: tip.x + 14, top: tip.y + 14 }} role="status">
          <strong>{tip.title}</strong>
          <span>{tip.detail}</span>
        </div>
      )}
      {placeholder !== null && placeholder !== undefined && <div className="map-placeholder">{placeholder}</div>}
    </div>
  );
}
