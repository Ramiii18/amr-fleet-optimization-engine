import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Inspector, type Tab } from "./components/Inspector";
import { MapCanvas, type MapHandle, type Selection } from "./components/MapCanvas";
import { PlaybackBar } from "./components/PlaybackBar";
import { RunPanel, type RunPhase } from "./components/RunPanel";
import { usePlaybackClock } from "./hooks";
import {
  ApiError,
  type Job,
  type MapDetail,
  type MapSummary,
  type RequestLimits,
  type SimulationOutcome,
  type SimulationRequest,
  api,
  limitsFromOpenApi,
} from "./lib/api";
import { bytes } from "./lib/format";
import { Playback } from "./lib/playback";
import { type Replay, boundsOf, buildReplay, corridorsOf, neighbourTick } from "./lib/replay";
import { type Preset, type RunForm, applyPreset, defaultForm, formOfRequest, validateForm } from "./lib/runForm";
import { TraceFormatError, parseTrace } from "./lib/trace";

const POLL_MS = 400;
const RECENT_SHOWN = 6;
const NO_SELECTION: Selection = { robot: null, node: null, order: null };

interface Loaded {
  replay: Replay;
  /** The job the run came from, or null for a trace file. */
  job: Job | null;
  label: string;
}

function message(error: unknown): string {
  if (error instanceof ApiError || error instanceof TraceFormatError) return error.message;
  return error instanceof Error ? error.message : String(error);
}

export function App() {
  const [service, setService] = useState<"checking" | "up" | "down">("checking");
  const [version, setVersion] = useState<string | null>(null);
  const [maps, setMaps] = useState<MapSummary[]>([]);
  const [policies, setPolicies] = useState<string[]>([]);
  const [limits, setLimits] = useState<RequestLimits | null>(null);
  const [form, setForm] = useState<RunForm>(() => defaultForm(null, [], []));
  const [mapDetail, setMapDetail] = useState<MapDetail | null>(null);
  const [phase, setPhase] = useState<RunPhase>({ kind: "idle" });
  const [recent, setRecent] = useState<Job[]>([]);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [selection, setSelection] = useState<Selection>(NO_SELECTION);
  const [tab, setTab] = useState<Tab>("map");
  const mapRef = useRef<MapHandle>(null);
  /** Counts runs, so that the answer to an older request cannot replace a newer one. */
  const runToken = useRef(0);

  const refreshRecent = useCallback(async (): Promise<void> => {
    try {
      const jobs = await api.jobs();
      setRecent(
        jobs
          .filter((job) => job.request.record_trace)
          .reverse()
          .slice(0, RECENT_SHOWN),
      );
    } catch {
      // The list is a convenience; the status line already says when the service is down.
    }
  }, []);

  /** Ask the service what it offers. Every state change happens after its answer. */
  const connect = useCallback((): Promise<void> => {
    return Promise.all([api.health(), api.maps(), api.policies(), api.openApi()]).then(
      ([health, mapList, policyList, document]) => {
        const found = limitsFromOpenApi(document);
        setVersion(health.version);
        setMaps(mapList);
        setPolicies(policyList);
        setLimits(found);
        setForm(
          defaultForm(
            found,
            mapList.map((map) => map.name),
            policyList,
          ),
        );
        setService("up");
        void refreshRecent();
      },
      () => setService("down"),
    );
  }, [refreshRecent]);

  useEffect(() => {
    void connect();
  }, [connect]);

  // The chosen map is shown as soon as it is chosen, before any run.
  useEffect(() => {
    if (service !== "up" || form.map === "") return;
    const controller = new AbortController();
    api
      .map(form.map, controller.signal)
      .then(setMapDetail)
      .catch(() => undefined);
    return () => controller.abort();
  }, [service, form.map]);

  const { problems } = useMemo(() => validateForm(form, limits), [form, limits]);

  const show = useCallback((replay: Replay, job: Job | null, label: string): void => {
    setLoaded({ replay, job, label });
    setSelection(NO_SELECTION);
    setTab("overview");
    setPhase({ kind: "idle" });
  }, []);

  const loadTrace = useCallback(
    async (job: Job, token: number): Promise<void> => {
      setPhase({ kind: "loading", job });
      try {
        const replay = buildReplay(parseTrace(await api.trace(job.id)));
        if (token !== runToken.current) return;
        show(replay, job, `Run ${job.id.slice(0, 8)} on the engine's service`);
      } catch (error) {
        if (token !== runToken.current) return;
        // 410: the service dropped the trace between the status and the request for it.
        if (error instanceof ApiError && error.status === 410) {
          setPhase({ kind: "no-trace", job, reason: "evicted" });
        } else {
          setPhase({ kind: "failed", message: message(error), job });
        }
      }
      void refreshRecent();
    },
    [refreshRecent, show],
  );

  const settle = useCallback(
    async (job: Job, token: number): Promise<void> => {
      if (job.status === "failed") {
        setPhase({ kind: "failed", message: job.error ?? "The simulation failed.", job });
        void refreshRecent();
      } else if (job.trace === "available") {
        await loadTrace(job, token);
      } else {
        const reason = job.trace === "evicted" || job.trace === "too_large" ? job.trace : "none";
        setPhase({ kind: "no-trace", job, reason });
        void refreshRecent();
      }
    },
    [loadTrace, refreshRecent],
  );

  const run = useCallback(
    async (request: SimulationRequest): Promise<void> => {
      const token = ++runToken.current;
      setPhase({ kind: "submitting" });
      try {
        let job = await api.submit(request);
        while (job.status === "queued" || job.status === "running") {
          if (token !== runToken.current) return;
          setPhase({ kind: "waiting", job });
          await new Promise((resolve) => setTimeout(resolve, POLL_MS));
          job = await api.job(job.id);
        }
        if (token !== runToken.current) return;
        await settle(job, token);
      } catch (error) {
        if (token !== runToken.current) return;
        if (error instanceof ApiError && error.status === 0) setService("down");
        setPhase({ kind: "failed", message: message(error), job: null });
      }
    },
    [settle],
  );

  const onRun = useCallback((): void => {
    const { request } = validateForm(form, limits);
    if (request !== null) void run(request);
  }, [form, limits, run]);

  const onRerun = useCallback(
    (job: Job): void => {
      setForm((current) => formOfRequest(job.request, current));
      void run({ ...job.request, record_trace: true });
    },
    [run],
  );

  const onLoadJob = useCallback(
    (job: Job): void => {
      const token = ++runToken.current;
      setForm((current) => formOfRequest(job.request, current));
      void loadTrace(job, token);
    },
    [loadTrace],
  );

  const onOpenFile = useCallback(
    async (file: File): Promise<void> => {
      const token = ++runToken.current;
      try {
        const replay = buildReplay(parseTrace(JSON.parse(await file.text())));
        if (token !== runToken.current) return;
        show(replay, null, `File ${file.name} (${bytes(file.size)})`);
      } catch (error) {
        const reason = error instanceof SyntaxError ? "it is not valid JSON" : message(error);
        setPhase({
          kind: "failed",
          title: "The file could not be opened.",
          message: `${file.name} cannot be played: ${reason}`,
          job: null,
        });
      }
    },
    [show],
  );

  const onPreset = useCallback(
    (preset: Preset): void => {
      setForm(
        applyPreset(
          preset,
          limits,
          maps.map((map) => map.name),
          policies,
        ),
      );
    },
    [limits, maps, policies],
  );

  const replay = loaded?.replay ?? null;
  const playback = useMemo(() => (replay === null ? null : new Playback(replay.endTick, replay.tickS)), [replay]);
  usePlaybackClock(playback);

  // What the map draws: the loaded run's own map, or else the map chosen in the form.
  const world = useMemo(() => {
    if (replay !== null) {
      return {
        nodes: replay.trace.map.nodes,
        nodeById: replay.nodes,
        corridors: replay.corridors,
        bounds: replay.bounds,
      };
    }
    const nodes = mapDetail?.nodes ?? [];
    const nodeById = new Map(nodes.map((node) => [node.id, node]));
    return {
      nodes,
      nodeById,
      corridors: corridorsOf(nodeById, mapDetail?.edges ?? []),
      bounds: boundsOf(nodes),
    };
  }, [replay, mapDetail]);

  const mapInfo = useMemo(
    () =>
      mapDetail === null
        ? null
        : { name: mapDetail.name, nodes: mapDetail.nodes, edges: mapDetail.edges, corridors: world.corridors },
    [mapDetail, world.corridors],
  );

  const select = useCallback((next: Selection, keepTab = false): void => {
    setSelection(next);
    if (keepTab) return;
    if (next.order !== null) setTab("orders");
    else if (next.robot !== null) setTab("robots");
    else if (next.node !== null) setTab("map");
  }, []);

  // Keyboard: the playback keys work anywhere except while typing in a field.
  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      const target = event.target as HTMLElement | null;
      if (target !== null && ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName)) return;
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const key = event.key;
      if (key === "f" || key === "F") mapRef.current?.fit();
      else if (key === "+" || key === "=") mapRef.current?.zoom(1.35);
      else if (key === "-") mapRef.current?.zoom(1 / 1.35);
      else if (key === "Escape") setSelection(NO_SELECTION);
      else if (playback === null || replay === null) return;
      else if (key === " ") {
        if (target !== null && target.tagName === "BUTTON") return; // Space presses the button
        playback.toggle();
      } else if (key === "ArrowRight") playback.stepSeconds(event.shiftKey ? 10 : 1);
      else if (key === "ArrowLeft") playback.stepSeconds(event.shiftKey ? -10 : -1);
      else if (key === ".") playback.stepTicks(1);
      else if (key === ",") playback.stepTicks(-1);
      else if (key === "Home") playback.restart();
      else if (key === "End") {
        playback.pause();
        playback.seek(replay.endTick);
      } else if (key === "]" || key === "[") {
        const next = neighbourTick(replay.eventTicks, playback.tick, key === "]" ? 1 : -1);
        if (next !== null) {
          playback.pause();
          playback.seek(next);
        }
      } else return;
      event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [playback, replay]);

  const outcome: SimulationOutcome | null = loaded?.job?.result ?? null;
  const placeholder =
    world.nodes.length > 0
      ? null
      : service === "checking"
        ? "Connecting to the engine…"
        : service === "down"
          ? "The engine's service is not reachable. Start amr-api, or open a trace file."
          : "Loading the map…";

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <svg viewBox="0 0 32 32" aria-hidden="true">
            <rect width="32" height="32" rx="7" />
            <path d="M7 22V10h6v12M19 22V10h6v12M13 16h6" />
          </svg>
          <div>
            <h1>AMR Fleet Digital Twin</h1>
            <p>Replay of simulated runs · Optimization Lab</p>
          </div>
        </div>
        <p className="disclosure" role="note">
          <strong>Synthetic simulation.</strong> No real robot is controlled or observed. Not a safety system.
        </p>
        <div className={`service ${service}`} role="status">
          <span className="dot" aria-hidden="true" />
          {service === "up" ? `Engine ${version ?? ""} connected` : service === "down" ? "Engine offline" : "Connecting…"}
        </div>
      </header>

      <aside className="left" aria-label="Simulation setup">
        <RunPanel
          service={service}
          maps={maps}
          policies={policies}
          limits={limits}
          form={form}
          problems={problems}
          phase={phase}
          recent={recent}
          loadedJobId={loaded?.job?.id ?? null}
          onChange={setForm}
          onPreset={onPreset}
          onRun={onRun}
          onLoadJob={onLoadJob}
          onRerun={onRerun}
          onOpenFile={(file) => void onOpenFile(file)}
          onRetryService={() => {
            setService("checking");
            void connect();
          }}
        />
      </aside>

      <main className="centre">
        <MapCanvas
          ref={mapRef}
          nodes={world.nodes}
          nodeById={world.nodeById}
          corridors={world.corridors}
          bounds={world.bounds}
          replay={replay}
          playback={playback}
          selection={selection}
          onSelect={select}
          placeholder={placeholder}
        />
        {replay !== null && playback !== null ? (
          <PlaybackBar replay={replay} playback={playback} />
        ) : (
          <div className="playback idle">
            {replay === null && world.nodes.length > 0
              ? "Exploring the map. Run a simulation to play it back here."
              : " "}
          </div>
        )}
      </main>

      <aside className="right" aria-label="Run details">
        <Inspector
          tab={tab}
          onTab={setTab}
          map={mapInfo}
          replay={replay}
          playback={playback}
          outcome={outcome}
          sourceLabel={loaded?.label ?? null}
          selection={selection}
          onSelect={select}
        />
      </aside>
    </div>
  );
}
