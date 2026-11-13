import { useMemo, useState } from "react";

import { usePlayhead } from "../hooks";
import type { SimulationOutcome } from "../lib/api";
import { DASH, clock, duration, metres, number, percent } from "../lib/format";
import { type Activity, activityAt, orderAt, poseAt } from "../lib/motion";
import type { Playback } from "../lib/playback";
import {
  ORDER_STATES,
  ORDER_STATE_LABEL,
  type Corridor,
  type OrderLife,
  type OrderState,
  type Replay,
  conflictsAt,
  drivenMetres,
  orderCounts,
  orderStateAt,
  robotLevelAt,
  statusCounts,
} from "../lib/replay";
import { type Level, STATUSES, STATUS_LABEL, STATUS_NOTE, type Status, statusesAt } from "../lib/state";
import type { NodeKind, RobotEvent, RobotEventKind, Scalar, TraceEdge, TraceNode } from "../lib/trace";
import { ACTIVITY_COLORS, ACTIVITY_LABEL, NODE_LABEL, STATUS_COLORS, STATUS_SHORT } from "../render/theme";
import type { Selection } from "./MapCanvas";

export type Tab = "overview" | "robots" | "orders" | "conflicts" | "map";

interface MapInfo {
  name: string;
  nodes: readonly TraceNode[];
  edges: readonly TraceEdge[];
  corridors: readonly Corridor[];
}

interface Props {
  tab: Tab;
  onTab: (tab: Tab) => void;
  map: MapInfo | null;
  replay: Replay | null;
  playback: Playback | null;
  outcome: SimulationOutcome | null;
  sourceLabel: string | null;
  selection: Selection;
  /** Select on the map and in the lists. With `keepTab`, the open tab stays open. */
  onSelect: (selection: Selection, keepTab?: boolean) => void;
}

const ORDER_COLORS: Record<OrderState, string> = {
  unreleased: "#2a3644",
  pending: "#f87171",
  to_pickup: "#f5b041",
  loading: "#fbd38d",
  carrying: "#34d399",
  unloading: "#86efac",
  delivered: "#38bdf8",
};

export function Inspector(props: Props) {
  const { tab, replay } = props;
  const tabs: { id: Tab; label: string; count?: number | undefined }[] = [
    { id: "overview", label: "Overview" },
    { id: "robots", label: "Robots", count: replay?.tracks.length },
    { id: "orders", label: "Orders", count: replay?.orders.length },
    { id: "conflicts", label: "Conflicts", count: replay?.trace.conflicts.total },
    { id: "map", label: "Map" },
  ];
  return (
    <div className="inspector">
      <div className="tabs" role="tablist" aria-label="Details">
        {tabs.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            className={tab === item.id ? "tab active" : "tab"}
            onClick={() => props.onTab(item.id)}
          >
            {item.label}
            {item.count !== undefined && (
              <span className={item.id === "conflicts" && item.count > 0 ? "tab-count alert" : "tab-count"}>
                {number(item.count)}
              </span>
            )}
          </button>
        ))}
      </div>
      <div className="tab-body" role="tabpanel">
        {tab === "map" ? (
          <MapTab {...props} />
        ) : replay === null || props.playback === null ? (
          <EmptyRun />
        ) : tab === "overview" ? (
          <Overview {...props} replay={replay} playback={props.playback} />
        ) : tab === "robots" ? (
          <Robots {...props} replay={replay} playback={props.playback} />
        ) : tab === "orders" ? (
          <Orders {...props} replay={replay} playback={props.playback} />
        ) : (
          <Conflicts {...props} replay={replay} playback={props.playback} />
        )}
      </div>
    </div>
  );
}

function EmptyRun() {
  return (
    <div className="empty">
      <strong>No run loaded</strong>
      <p>
        Choose a scenario on the left and run a simulation. The engine simulates it, records every robot&apos;s
        movement, and this viewer plays the recording back.
      </p>
      <p>The Map tab shows the warehouse you have chosen.</p>
    </div>
  );
}

type RunProps = Props & { replay: Replay; playback: Playback };

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "alert" | "good" }) {
  return (
    <div className={tone ? `stat ${tone}` : "stat"}>
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {sub && <span className="stat-sub">{sub}</span>}
    </div>
  );
}

function seconds(value: Scalar | undefined): string {
  return typeof value === "number" ? duration(value) : DASH;
}

function runSetting(run: Record<string, unknown>, key: string): string | null {
  const value = run[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : null;
}

function Overview({ replay, playback, outcome, sourceLabel }: RunProps) {
  const tick = usePlayhead(playback, 5);
  const { trace } = replay;
  const kpis = trace.kpis;
  const counts = orderCounts(replay.orders, tick);
  const activities: Record<Activity, number> = { free: 0, "to pickup": 0, loaded: 0 };
  for (const track of replay.tracks) activities[activityAt(replay.activity.get(track.id), tick)] += 1;
  const statuses = statusCounts(replay, tick);
  const orders = replay.orders.length;
  const charging = trace.charging;
  const run = trace.run;
  const where = runSetting(run, "map") ?? runSetting(run, "scenario");
  const seed = runSetting(run, "seed");
  const undelivered = typeof kpis["undelivered"] === "number" ? kpis["undelivered"] : 0;

  return (
    <>
      <div className="run-head">
        <div className="chips">
          <span className="chip strong">{trace.policy}</span>
          {where !== null && <span className="chip">{where}</span>}
          <span className="chip">{replay.tracks.length} robots</span>
          {seed !== null && <span className="chip">seed {seed}</span>}
          <span className={trace.traffic ? "chip" : "chip warn"}>{trace.traffic ? "Traffic managed" : "Free flow"}</span>
          {trace.battery && <span className="chip">Battery model</span>}
        </div>
        {sourceLabel !== null && <p className="muted small">{sourceLabel}</p>}
      </div>

      <h3>At the playhead · {clock(tick * replay.tickS)}</h3>
      <div className="live">
        <div className="bar" role="img" aria-label="Orders by state at the playhead">
          {ORDER_STATES.map((state) =>
            counts[state] > 0 ? (
              <span
                key={state}
                style={{ flexGrow: counts[state], background: ORDER_COLORS[state] }}
                title={`${ORDER_STATE_LABEL[state]}: ${counts[state]}`}
              />
            ) : null,
          )}
        </div>
        <ul className="key">
          {ORDER_STATES.filter((state) => counts[state] > 0 || state === "delivered" || state === "pending").map((state) => (
            <li key={state}>
              <span className="swatch" style={{ background: ORDER_COLORS[state] }} />
              {ORDER_STATE_LABEL[state]}
              <b>{counts[state]}</b>
            </li>
          ))}
        </ul>
        <ul className="key robots">
          {(Object.keys(activities) as Activity[]).map((activity) => (
            <li key={activity}>
              <span className="swatch round" style={{ background: ACTIVITY_COLORS[activity] }} />
              Robots {ACTIVITY_LABEL[activity].toLowerCase()}
              <b>{activities[activity]}</b>
            </li>
          ))}
        </ul>
        {replay.observed ? (
          <ul className="key robots">
            {STATUSES.filter((status) => statuses[status] > 0 || status === "charging" || status === "breakdown").map(
              (status) => (
                <li key={status} title={STATUS_NOTE[status]}>
                  <span className="swatch round" style={{ background: STATUS_COLORS[status] }} />
                  Robots: {STATUS_LABEL[status].toLowerCase()}
                  <b>{statuses[status]}</b>
                </li>
              ),
            )}
          </ul>
        ) : (
          <p className="muted small">
            Charging, service state, breakdowns and waits are not recorded in this trace (format version 1), so
            they are unknown for this run, not absent from it.
          </p>
        )}
      </div>

      <h3>Whole run · computed by the engine</h3>
      <div className="stats">
        <Stat
          label="Delivered"
          value={`${number(kpis["delivered"])} / ${number(orders)}`}
          sub={undelivered > 0 ? `${undelivered} not delivered` : "every order delivered"}
          tone={undelivered > 0 ? "alert" : "good"}
        />
        <Stat label="Throughput" value={number(kpis["throughput_per_h"], 1)} sub="orders per hour" />
        <Stat label="Completion, mean" value={seconds(kpis["completion_mean_s"])} sub="release to delivery" />
        <Stat label="Completion, P95" value={seconds(kpis["completion_p95_s"])} sub={`worst ${seconds(kpis["completion_max_s"])}`} />
        <Stat label="Waiting, mean" value={seconds(kpis["waiting_mean_s"])} sub={`P95 ${seconds(kpis["waiting_p95_s"])}`} />
        <Stat label="Utilization" value={percent(kpis["utilization"])} sub="robot time on a job" />
        <Stat
          label="Empty travel"
          value={percent(kpis["empty_share"])}
          sub={`${metres(kpis["empty_distance_m"])} of ${metres(
            typeof kpis["empty_distance_m"] === "number" && typeof kpis["loaded_distance_m"] === "number"
              ? kpis["empty_distance_m"] + kpis["loaded_distance_m"]
              : null,
          )}`}
        />
        <Stat
          label="Conflicts"
          value={number(trace.conflicts.total)}
          sub={trace.traffic ? "found by the checker" : "traffic would prevent these"}
          tone={trace.conflicts.total > 0 ? "alert" : "good"}
        />
      </div>

      <dl className="facts">
        <dt>Simulated time</dt>
        <dd>{seconds(kpis["duration_s"])}</dd>
        <dt>Assignment delay, mean</dt>
        <dd>{seconds(kpis["assignment_delay_mean_s"])}</dd>
        <dt>Breakdowns</dt>
        <dd>
          {number(trace.counters.breakdowns)}
          {trace.counters.breakdowns > 0 && ` · ${duration(trace.counters.breakdown_ticks * replay.tickS)} of repairs`}
        </dd>
        <dt>Replans adopted</dt>
        <dd>{number(trace.counters.replans)}</dd>
        <dt>Robots moved out of the way</dt>
        <dd>{number(trace.counters.moves_out_of_the_way)}</dd>
        <dt>Assignments deferred</dt>
        <dd>{number(trace.counters.deferred_assignments)}</dd>
      </dl>
      {(trace.counters.breakdowns > 0 || charging !== null) && (
        <p className="muted small">
          {replay.observed
            ? "These are totals of the run. When each breakdown and each charge happened is in the run's robot events: see the Robots tab and the badges on the map."
            : "These are totals of the run. This trace (format version 1) does not record when each one happened, so the map does not show them."}
        </p>
      )}

      {charging !== null && (
        <>
          <h3>Charging · totals of the run</h3>
          <dl className="facts">
            <dt>Charging trips when low</dt>
            <dd>{number(charging["charging_trips"])}</dd>
            <dt>Top-ups while idle</dt>
            <dd>{number(charging["top_up_trips"])}</dd>
            <dt>Robot time out of service</dt>
            <dd>{percent(charging["out_of_service_share"])}</dd>
            <dt>Waiting for a charger</dt>
            <dd>{seconds(charging["charger_wait_s"])}</dd>
            <dt>Charger occupancy</dt>
            <dd>{percent(charging["charger_occupancy"])}</dd>
            <dt>Lowest battery level</dt>
            <dd>{percent(charging["lowest_energy_share"], 0)}</dd>
          </dl>
        </>
      )}

      {outcome !== null && (
        <>
          <h3>Computation · wall clock</h3>
          <dl className="facts">
            <dt>Time to simulate the run</dt>
            <dd>{outcome.compute_s.toFixed(2)} s</dd>
          </dl>
          <p className="muted small">Measured on this machine; it is not part of the simulated time above.</p>
        </>
      )}
    </>
  );
}

function ActivityChip({ activity }: { activity: Activity }) {
  return (
    <span className="activity" style={{ color: ACTIVITY_COLORS[activity] }}>
      <span className="swatch round" style={{ background: ACTIVITY_COLORS[activity] }} />
      {ACTIVITY_LABEL[activity]}
    </span>
  );
}

function Robots({ replay, playback, selection, onSelect }: RunProps) {
  const tick = usePlayhead(playback, 5);
  const selected = selection.robot === null ? undefined : replay.trackById.get(selection.robot);
  return (
    <>
      {selected !== undefined && <RobotDetail replay={replay} playback={playback} robotId={selected.id} tick={tick} onSelect={onSelect} />}
      <h3>Fleet · {replay.tracks.length} robots</h3>
      <ul className="rows">
        {replay.tracks.map((track) => {
          const pose = poseAt(track, tick);
          const activity = activityAt(replay.activity.get(track.id), tick);
          const from = track.nodes[pose.visit] ?? "";
          const to = track.nodes[pose.visit + 1] ?? "";
          const statuses = statusesAt(replay.statuses.get(track.id), tick);
          const level = robotLevelAt(replay, track.id, tick);
          return (
            <li key={track.id}>
              <button
                type="button"
                className={selection.robot === track.id ? "row robot selected" : "row robot"}
                onClick={() => onSelect({ robot: track.id, node: null, order: null })}
              >
                <span className="mono id">{track.id}</span>
                <ActivityChip activity={activity} />
                <span className="mono where">{pose.moving ? `${from} → ${to}` : from}</span>
                <span className="row-extra">
                  {level !== null && replay.battery !== null && (
                    <span className="mono battery-figure" title={levelTitle(level, replay)}>
                      {percent(level.units / replay.battery.capacity, 0)}
                    </span>
                  )}
                  {statuses.map((status) => (
                    <span key={status} className="status-chip" style={{ color: STATUS_COLORS[status] }} title={STATUS_NOTE[status]}>
                      {STATUS_SHORT[status]}
                    </span>
                  ))}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </>
  );
}

function RobotDetail({
  replay,
  playback,
  robotId,
  tick,
  onSelect,
}: {
  replay: Replay;
  playback: Playback;
  robotId: string;
  tick: number;
  onSelect: (selection: Selection) => void;
}) {
  const track = replay.trackById.get(robotId);
  const changes = replay.activity.get(robotId);
  const segments = useMemo(() => {
    // The robot's activity over the whole run, as stretches between its logged changes.
    const out: { from: number; to: number; activity: Activity }[] = [];
    if (changes === undefined) return out;
    let from = 0;
    let activity: Activity = "free";
    changes.ticks.forEach((at, index) => {
      if (at > from) out.push({ from, to: at, activity });
      from = at;
      activity = changes.activities[index] as Activity;
    });
    if (replay.endTick > from) out.push({ from, to: replay.endTick, activity });
    return out;
  }, [changes, replay.endTick]);
  if (track === undefined) return null;

  const pose = poseAt(track, tick);
  const timeline = replay.statuses.get(robotId);
  const statuses = statusesAt(timeline, tick);
  const disturbed = timeline?.intervals.filter(
    (interval) =>
      (interval.status === "breakdown" || interval.status === "delay") &&
      interval.start <= tick &&
      tick < interval.end,
  );
  const level = robotLevelAt(replay, robotId, tick);
  const events = replay.robotEvents.get(robotId) ?? [];
  const activity = activityAt(changes, tick);
  const orderId = orderAt(changes, tick);
  const order = orderId === null ? undefined : replay.orderById.get(orderId);
  const here = track.nodes[pose.visit] ?? "";
  const next = track.nodes[pose.visit + 1];
  const node = replay.nodes.get(here);
  const arrived = track.arrive[pose.visit] ?? 0;
  const end = replay.endTick;

  return (
    <div className="detail">
      <div className="detail-head">
        <span className="mono title">{robotId}</span>
        <ActivityChip activity={activity} />
        <button type="button" className="link" onClick={() => onSelect({ robot: null, node: null, order: null })}>
          Clear
        </button>
      </div>
      <dl className="facts">
        <dt>Movement</dt>
        <dd>
          {pose.waiting && next !== undefined ? (
            <>
              At the end of <span className="mono">{here}</span> → <span className="mono">{next}</span>, waiting to
              enter <span className="mono">{next}</span>
            </>
          ) : pose.moving && next !== undefined ? (
            <>
              Driving <span className="mono">{here}</span> → <span className="mono">{next}</span>
              {disturbed !== undefined && disturbed.length > 0
                ? " · place on the edge not modelled"
                : ` · ${Math.round(pose.share * 100)}% of the edge`}
            </>
          ) : (
            <>
              Standing at <span className="mono">{here}</span>
              {node !== undefined && ` (${NODE_LABEL[node.kind].toLowerCase()})`} for{" "}
              {duration(Math.max(0, tick - arrived) * replay.tickS)}
            </>
          )}
        </dd>
        <dt>Position</dt>
        <dd className="mono">
          x {pose.x.toFixed(2)} m · y {pose.y.toFixed(2)} m
        </dd>
        <dt>Order</dt>
        <dd>
          {order === undefined ? (
            "None"
          ) : (
            <button type="button" className="link mono" onClick={() => onSelect({ robot: robotId, node: null, order: order.id })}>
              {order.id}
            </button>
          )}
          {order !== undefined && (
            <>
              {" "}
              · <span className="mono">{order.pickup}</span> → <span className="mono">{order.dropoff}</span>
            </>
          )}
        </dd>
        <dt>Driven so far</dt>
        <dd>{metres(drivenMetres(replay, robotId, pose.visit, pose.share))} along its recorded path</dd>
        <dt>Battery</dt>
        <dd>
          {!replay.observed ? (
            <span className="muted">Not recorded in this trace (format version 1)</span>
          ) : level === null || replay.battery === null ? (
            <span className="muted">This run has no battery model</span>
          ) : (
            <>
              <span className="mono">{percent(level.units / replay.battery.capacity, 1)}</span> ·{" "}
              {metres(level.units / replay.battery.units_per_metre)} of driving
            </>
          )}
        </dd>
      </dl>
      {level !== null && replay.battery !== null && (
        <>
          <div
            className="battery"
            role="img"
            aria-label={`Battery ${percent(level.units / replay.battery.capacity, 0)}`}
          >
            <span
              className={level.units < replay.battery.low ? "battery-fill low" : "battery-fill"}
              style={{ width: `${(level.units / replay.battery.capacity) * 100}%` }}
            />
            <i style={{ left: `${(replay.battery.low / replay.battery.capacity) * 100}%` }} title="Low threshold" />
            <i style={{ left: `${(replay.battery.high / replay.battery.capacity) * 100}%` }} title="High threshold" />
          </div>
          <p className="muted small">{levelTitle(level, replay, tick)}</p>
        </>
      )}
      {statuses.length > 0 && (
        <ul className="status-list">
          {statuses.map((status) => {
            const interval = timeline?.intervals.find(
              (candidate) => candidate.status === status && candidate.start <= tick && tick < candidate.end,
            );
            return (
              <li key={status}>
                <strong style={{ color: STATUS_COLORS[status] }}>
                  {STATUS_LABEL[status]}
                  {interval !== undefined &&
                    interval.event.ticks !== null &&
                    ` · ${duration(interval.event.ticks * replay.tickS)}`}
                </strong>
                <span>
                  {STATUS_NOTE[status]}
                  {interval !== undefined && ` Since ${clock(interval.start * replay.tickS)}.`}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      <div className="strip-label">
        <span>Activity over the run</span>
        <span className="muted">click to jump</span>
      </div>
      <div
        className="strip"
        role="img"
        aria-label={`Activity of ${robotId} over the run`}
        onClick={(event) => {
          const box = event.currentTarget.getBoundingClientRect();
          playback.seek(((event.clientX - box.left) / box.width) * end);
        }}
      >
        {segments.map((segment) => (
          <span
            key={segment.from}
            style={{
              left: `${(segment.from / end) * 100}%`,
              width: `${((segment.to - segment.from) / end) * 100}%`,
              background: ACTIVITY_COLORS[segment.activity],
              opacity: segment.activity === "free" ? 0.28 : 1,
            }}
          />
        ))}
        <i style={{ left: `${end > 0 ? (tick / end) * 100 : 0}%` }} />
      </div>
      {replay.observed && (timeline?.intervals.length ?? 0) > 0 && (
        <>
          <div className="strip-label">
            <span>Status over the run</span>
            <span className="muted">from the robot&apos;s events</span>
          </div>
          <div
            className="strip status"
            role="img"
            aria-label={`Statuses of ${robotId} over the run`}
            onClick={(event) => {
              const box = event.currentTarget.getBoundingClientRect();
              playback.seek(((event.clientX - box.left) / box.width) * end);
            }}
          >
            {timeline?.intervals.map((interval, index) => (
              <span
                key={index}
                className={`lane-${STATUS_LANE[interval.status]}`}
                title={STATUS_LABEL[interval.status]}
                style={{
                  left: `${(interval.start / end) * 100}%`,
                  width: `max(2px, ${((Math.min(interval.end, end) - interval.start) / end) * 100}%)`,
                  background: STATUS_COLORS[interval.status],
                }}
              />
            ))}
            <i style={{ left: `${end > 0 ? (tick / end) * 100 : 0}%` }} />
          </div>
        </>
      )}
      <p className="muted small">
        {replay.observed
          ? "Free, to pickup and loaded come from the order log; charging, service state, delays, breakdowns and waits from the robot's events."
          : "Free, to pickup and loaded come from the order log. This trace (format version 1) records no other state, such as charging or a breakdown."}
      </p>
      {replay.observed && (
        <>
          <h3>Events of {robotId}</h3>
          {events.length === 0 ? (
            <p className="muted small">Nothing besides its orders happened to this robot in this run.</p>
          ) : (
            <ul className="rows">
              {events.slice(0, LIST_LIMIT).map((event, index) => (
                <li key={index}>
                  <button
                    type="button"
                    className={event.tick <= tick ? "row event past" : "row event"}
                    onClick={() => {
                      playback.pause();
                      playback.seek(event.tick);
                    }}
                  >
                    <span className="mono id">{clock(event.tick * replay.tickS)}</span>
                    <span className="conflict-what">
                      <span>{EVENT_LABEL[event.kind]}</span>
                      <span className="mono muted">{eventDetail(event, replay)}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {events.length > LIST_LIMIT && (
            <p className="muted small">
              Showing the first {LIST_LIMIT} of {number(events.length)} events. The strip above shows all of them.
            </p>
          )}
        </>
      )}
    </div>
  );
}

/** Which row of the status strip each status is drawn in. */
const STATUS_LANE: Record<Status, number> = {
  charging: 0,
  out_of_service: 1,
  waiting_for_charger: 1,
  breakdown: 2,
  delay: 2,
  corridor_wait: 2,
};

const EVENT_LABEL: Record<RobotEventKind, string> = {
  charging_started: "Charging started",
  charging_stopped: "Charging stopped",
  out_of_service: "Out of service (battery low)",
  back_in_service: "Back in service",
  charger_wait_started: "Waiting for a charger",
  charger_wait_ended: "A charger was found",
  charger_trip_started: "Sent to a charger",
  delay: "Delay on a traversal",
  breakdown: "Breakdown on a traversal",
  corridor_wait_started: "Waiting at the corridor end",
  corridor_wait_ended: "Entered the node",
  replanned: "New route adopted",
  moved_out_of_the_way: "Moved out of the way",
};

function eventDetail(event: RobotEvent, replay: Replay): string {
  const parts: string[] = [event.target === null ? event.node : `${event.node} → ${event.target}`];
  if (event.ticks !== null) parts.push(`+${duration(event.ticks * replay.tickS)}`);
  if (event.reason !== null) parts.push(event.reason === "low" ? "battery low" : "top-up while idle");
  if (event.level !== null && replay.battery !== null) {
    parts.push(`battery ${percent(event.level / replay.battery.capacity, 1)}`);
  }
  return parts.join(" · ");
}

/** Where a battery value comes from, in words: a reading and its age, or the charging formula. */
function levelTitle(level: Level, replay: Replay, tick?: number): string {
  if (level.source === "charging_formula") {
    return "Calculated with the engine's charging formula while the robot stands on a charger. It is not a recorded reading.";
  }
  const when = `Engine reading at ${clock(level.asOf * replay.tickS)}`;
  if (tick === undefined || tick - level.asOf < 1) return `${when}.`;
  return `${when}, ${duration((tick - level.asOf) * replay.tickS)} ago. The engine records a level on arrival at a node, so no later value exists yet; none is estimated.`;
}

const STEPS: { key: keyof OrderLife; label: string }[] = [
  { key: "released", label: "Released" },
  { key: "assigned", label: "Assigned to a robot" },
  { key: "pickupReached", label: "Robot at the pickup" },
  { key: "loaded", label: "Loaded" },
  { key: "dropoffReached", label: "Robot at the drop-off" },
  { key: "delivered", label: "Delivered" },
];

const LIST_LIMIT = 150;

function Orders({ replay, playback, selection, onSelect }: RunProps) {
  const tick = usePlayhead(playback, 4);
  const [filter, setFilter] = useState<OrderState | "all">("all");
  const counts = orderCounts(replay.orders, tick);
  const selected = selection.order === null ? undefined : replay.orderById.get(selection.order);
  const matching = replay.orders.filter((order) => filter === "all" || orderStateAt(order, tick) === filter);
  const shown = matching.slice(0, LIST_LIMIT);

  return (
    <>
      {selected !== undefined && (
        <div className="detail">
          <div className="detail-head">
            <span className="mono title">{selected.id}</span>
            <span className="state" style={{ color: ORDER_COLORS[orderStateAt(selected, tick)] }}>
              {ORDER_STATE_LABEL[orderStateAt(selected, tick)]}
            </span>
            <button type="button" className="link" onClick={() => onSelect({ ...selection, order: null })}>
              Clear
            </button>
          </div>
          <dl className="facts">
            <dt>Route</dt>
            <dd>
              <span className="mono">{selected.pickup}</span> → <span className="mono">{selected.dropoff}</span>
            </dd>
            <dt>Robot</dt>
            <dd>
              {selected.robot === null ? (
                "Not assigned"
              ) : (
                <button
                  type="button"
                  className="link mono"
                  onClick={() => onSelect({ robot: selected.robot, node: null, order: selected.id })}
                >
                  {selected.robot}
                </button>
              )}
            </dd>
            <dt>Planned distance</dt>
            <dd>
              {selected.assigned === null
                ? DASH
                : `${metres(selected.emptyM)} empty${selected.loaded === null ? "" : ` · ${metres(selected.loadedM)} loaded`}`}
            </dd>
            <dt>Completion time</dt>
            <dd>
              {selected.delivered !== null && selected.released !== null
                ? duration((selected.delivered - selected.released) * replay.tickS)
                : "Not delivered in this run"}
            </dd>
          </dl>
          <ol className="lifecycle">
            {STEPS.map((step, index) => {
              const at = selected[step.key] as number | null;
              const previous = index === 0 ? null : (selected[STEPS[index - 1]?.key ?? "released"] as number | null);
              const done = at !== null && at <= tick;
              return (
                <li key={step.key} className={at === null ? "never" : done ? "done" : "ahead"}>
                  <button type="button" disabled={at === null} onClick={() => at !== null && (playback.pause(), playback.seek(at))}>
                    <span className="step-name">{step.label}</span>
                    <span className="mono step-time">{at === null ? DASH : clock(at * replay.tickS)}</span>
                    <span className="step-gap">
                      {at !== null && previous !== null ? `+${duration((at - previous) * replay.tickS)}` : ""}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
          <p className="muted small">Each step is one entry of the engine&apos;s event log. Click a step to jump to it.</p>
        </div>
      )}

      <h3>Orders at {clock(tick * replay.tickS)}</h3>
      <div className="filters">
        <button type="button" className={filter === "all" ? "filter on" : "filter"} onClick={() => setFilter("all")}>
          All <b>{replay.orders.length}</b>
        </button>
        {ORDER_STATES.map((state) => (
          <button
            key={state}
            type="button"
            className={filter === state ? "filter on" : "filter"}
            onClick={() => setFilter(state)}
            disabled={counts[state] === 0 && filter !== state}
          >
            <span className="swatch" style={{ background: ORDER_COLORS[state] }} />
            {ORDER_STATE_LABEL[state]} <b>{counts[state]}</b>
          </button>
        ))}
      </div>
      {matching.length === 0 ? (
        <p className="muted small">No order is in this state at this moment.</p>
      ) : (
        <ul className="rows">
          {shown.map((order) => {
            const state = orderStateAt(order, tick);
            return (
              <li key={order.id}>
                <button
                  type="button"
                  className={selection.order === order.id ? "row selected" : "row"}
                  onClick={() => onSelect({ robot: order.robot ?? selection.robot, node: null, order: order.id })}
                >
                  <span className="mono id">{order.id}</span>
                  <span className="state" style={{ color: ORDER_COLORS[state] === "#2a3644" ? "#7f8fa3" : ORDER_COLORS[state] }}>
                    {ORDER_STATE_LABEL[state]}
                  </span>
                  <span className="mono where">
                    {order.pickup} → {order.dropoff}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {matching.length > shown.length && (
        <p className="muted small">
          Showing the first {shown.length} of {number(matching.length)}. Choose a state above to narrow the list.
        </p>
      )}
    </>
  );
}

const CONFLICT_LABEL: Record<string, string> = {
  vertex: "Same node",
  "head-on": "Corridor, opposite directions",
  following: "Corridor, same direction",
};

function Conflicts({ replay, playback, onSelect }: RunProps) {
  const tick = usePlayhead(playback, 4);
  const { trace } = replay;
  const total = trace.conflicts.total;
  const active = new Set(conflictsAt(replay.conflicts, tick));
  const shown = replay.conflicts.slice(0, LIST_LIMIT);
  return (
    <>
      <div className="stats two">
        <Stat label="Conflicts in the run" value={number(total)} tone={total > 0 ? "alert" : "good"} />
        <Stat label="Happening now" value={number(active.size)} sub={`at ${clock(tick * replay.tickS)}`} />
      </div>
      <p className="muted small">
        A conflict is two robots in the same node, or in the same corridor, at a common moment. The engine&apos;s
        independent checker finds them in the recorded movements; this viewer only displays its list. They are
        conflicts of the graph model, not measured physical collisions.
      </p>
      {total === 0 ? (
        <div className="empty good">
          <strong>No conflicts</strong>
          <p>
            {trace.traffic
              ? "With traffic management, every robot reserved its nodes and corridors. The checker confirms that no two robots ever shared one."
              : "The checker found none in this run."}
          </p>
        </div>
      ) : (
        <>
          {!trace.traffic && (
            <p className="muted small">
              This run was made in free flow: robots ignore each other. These are the conflicts that traffic
              management has to prevent.
            </p>
          )}
          <h3>
            {replay.conflicts.length < total
              ? `Earliest ${number(replay.conflicts.length)} of ${number(total)}`
              : "All conflicts, in time order"}
          </h3>
          <ul className="rows">
            {shown.map((conflict, index) => (
              <li key={index}>
                <button
                  type="button"
                  className={active.has(conflict) ? "row conflict now" : "row conflict"}
                  onClick={() => {
                    playback.pause();
                    playback.seek(conflict.start);
                    onSelect({ robot: conflict.robots[0], node: conflict.resource[0] ?? null, order: null }, true);
                  }}
                >
                  <span className="mono id">{clock(conflict.start * replay.tickS)}</span>
                  <span className="conflict-what">
                    <span>{CONFLICT_LABEL[conflict.kind] ?? conflict.kind}</span>
                    <span className="mono muted">
                      {conflict.resource.join(" – ")} · {conflict.robots.join(" & ")} ·{" "}
                      {duration((conflict.end - conflict.start) * replay.tickS)}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {replay.conflicts.length > shown.length && (
            <p className="muted small">
              Showing the first {shown.length} here. All {number(replay.conflicts.length)} listed conflicts are
              marked on the map and on the timeline.
            </p>
          )}
        </>
      )}
    </>
  );
}

function MapTab({ map, replay, selection, onSelect }: Props) {
  const info = useMemo(
    (): MapInfo | null =>
      replay !== null
        ? {
            name: runSetting(replay.trace.run, "map") ?? runSetting(replay.trace.run, "scenario") ?? "the run's map",
            nodes: replay.trace.map.nodes,
            edges: replay.trace.map.edges,
            corridors: replay.corridors,
          }
        : map,
    [replay, map],
  );
  const stats = useMemo(() => {
    if (info === null) return null;
    const kinds = new Map<NodeKind, number>();
    for (const node of info.nodes) kinds.set(node.kind, (kinds.get(node.kind) ?? 0) + 1);
    const xs = info.nodes.map((node) => node.x);
    const ys = info.nodes.map((node) => node.y);
    return {
      kinds,
      width: xs.length > 0 ? Math.max(...xs) - Math.min(...xs) : 0,
      height: ys.length > 0 ? Math.max(...ys) - Math.min(...ys) : 0,
      oneWay: info.corridors.filter((corridor) => corridor.oneWay).length,
      twoWay: info.corridors.filter((corridor) => !corridor.oneWay).length,
      length: info.corridors.reduce((sum, corridor) => sum + corridor.lengthM, 0),
    };
  }, [info]);
  if (info === null || stats === null) {
    return (
      <div className="empty">
        <strong>No map loaded</strong>
        <p>The map appears here once the engine&apos;s service answers.</p>
      </div>
    );
  }
  const node = selection.node === null ? undefined : info.nodes.find((candidate) => candidate.id === selection.node);
  const out = node === undefined ? [] : info.edges.filter((edge) => edge.source === node.id);
  const into = node === undefined ? [] : info.edges.filter((edge) => edge.target === node.id);

  return (
    <>
      {node !== undefined && (
        <div className="detail">
          <div className="detail-head">
            <span className="mono title">{node.id}</span>
            <span className="state">{NODE_LABEL[node.kind]}</span>
            <button type="button" className="link" onClick={() => onSelect({ ...selection, node: null })}>
              Clear
            </button>
          </div>
          <dl className="facts">
            <dt>Position</dt>
            <dd className="mono">
              x {node.x.toFixed(2)} m · y {node.y.toFixed(2)} m
            </dd>
            <dt>Robots can leave to</dt>
            <dd className="mono wrap">
              {out.length === 0 ? DASH : out.map((edge) => `${edge.target} (${edge.length_m.toFixed(1)} m)`).join(", ")}
            </dd>
            <dt>Robots can arrive from</dt>
            <dd className="mono wrap">{into.length === 0 ? DASH : into.map((edge) => edge.source).join(", ")}</dd>
          </dl>
        </div>
      )}
      <h3>Warehouse · {info.name}</h3>
      <div className="stats two">
        <Stat label="Nodes" value={number(info.nodes.length)} sub="places a robot can be" />
        <Stat label="Directed edges" value={number(info.edges.length)} sub={`${number(stats.length, 0)} m of corridors`} />
        <Stat label="Two-way corridors" value={number(stats.twoWay)} />
        <Stat label="One-way connections" value={number(stats.oneWay)} sub="drawn with an arrow" />
      </div>
      <dl className="facts">
        <dt>Extent</dt>
        <dd>
          {stats.width.toFixed(0)} m × {stats.height.toFixed(0)} m
        </dd>
        {[...stats.kinds].map(([kind, count]) => (
          <div key={kind} className="fact-pair">
            <dt>{NODE_LABEL[kind]}</dt>
            <dd>{count}</dd>
          </div>
        ))}
      </dl>
      <p className="muted small">
        The map is a graph: points and the connections between them, with their driven lengths. It has no aisle
        widths and no robot footprint, so none are drawn. Click a node on the map to inspect it.
      </p>
    </>
  );
}
