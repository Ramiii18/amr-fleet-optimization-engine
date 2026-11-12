import { useRef } from "react";

import type { Job, MapSummary, RequestLimits } from "../lib/api";
import { duration } from "../lib/format";
import { type DisturbanceForm, PRESETS, type Preset, type Problems, type RunForm } from "../lib/runForm";

/** Where the current run is on its way from the form to the screen. */
export type RunPhase =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "waiting"; job: Job }
  | { kind: "loading"; job: Job }
  | { kind: "failed"; message: string; job: Job | null; title?: string }
  | { kind: "no-trace"; job: Job; reason: "evicted" | "too_large" | "none" };

interface Props {
  service: "checking" | "up" | "down";
  maps: readonly MapSummary[];
  policies: readonly string[];
  limits: RequestLimits | null;
  form: RunForm;
  problems: Problems;
  phase: RunPhase;
  recent: readonly Job[];
  loadedJobId: string | null;
  onChange: (form: RunForm) => void;
  onPreset: (preset: Preset) => void;
  onRun: () => void;
  onLoadJob: (job: Job) => void;
  onRerun: (job: Job) => void;
  onOpenFile: (file: File) => void;
  onRetryService: () => void;
}

const CHARGING_LABEL: Record<string, string> = {
  "when-low": "Charge when low",
  opportunistic: "Also top up when idle",
  "opportunistic-early": "Top up, back in service early",
};

const RECOVERY_LABEL: Record<string, string> = {
  wait: "Wait behind the broken robot",
  retime: "Re-time the plans",
  replan: "Re-time and replan",
};

function Field({
  label,
  unit,
  problem,
  hint,
  children,
}: {
  label: string;
  unit?: string;
  problem?: string | undefined;
  hint?: string | undefined;
  children: React.ReactNode;
}) {
  return (
    <label className={problem ? "field invalid" : "field"}>
      <span className="field-label">
        {label}
        {unit && <span className="unit">{unit}</span>}
      </span>
      {children}
      {problem ? <span className="field-problem">{problem}</span> : hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

function DisturbanceFields({
  name,
  value,
  problems,
  disabled,
  onChange,
}: {
  name: string;
  value: DisturbanceForm;
  problems: Problems;
  disabled: boolean;
  onChange: (value: DisturbanceForm) => void;
}) {
  if (!value.on || disabled) return null;
  return (
    <div className="field-row three">
      <Field label="Share" unit="%" problem={problems[`${name}.share`]} hint="of corridor entries">
        <input
          inputMode="decimal"
          value={value.sharePercent}
          onChange={(event) => onChange({ ...value, sharePercent: event.target.value })}
        />
      </Field>
      <Field label="Min" unit="s" problem={problems[`${name}.min_s`]}>
        <input inputMode="decimal" value={value.minS} onChange={(event) => onChange({ ...value, minS: event.target.value })} />
      </Field>
      <Field label="Max" unit="s" problem={problems[`${name}.max_s`]}>
        <input inputMode="decimal" value={value.maxS} onChange={(event) => onChange({ ...value, maxS: event.target.value })} />
      </Field>
    </div>
  );
}

function range(limit: { min: number; max: number; exclusive: boolean } | undefined): string | undefined {
  if (limit === undefined) return undefined;
  return limit.exclusive ? `up to ${limit.max}` : `${limit.min} to ${limit.max}`;
}

function describe(job: Job): string {
  const r = job.request;
  return `${r.map} · ${r.policy} · ${r.robots} robots · seed ${r.seed}`;
}

const TRACE_NOTE: Record<string, string> = {
  available: "Trace available",
  pending: "Running",
  evicted: "Trace dropped by the service",
  too_large: "Trace too large to keep",
  none: "No trace",
};

export function RunPanel(props: Props) {
  const { form, problems, limits, phase, service } = props;
  const fileRef = useRef<HTMLInputElement>(null);
  const set = (change: Partial<RunForm>): void => props.onChange({ ...form, ...change });
  const busy = phase.kind === "submitting" || phase.kind === "waiting" || phase.kind === "loading";
  const invalid = Object.keys(problems).length > 0;
  const seconds = Number(form.durationS);

  return (
    <div className="run-panel">
      <section className="panel-section">
        <h2>Scenario</h2>
        <div className="presets">
          {PRESETS.map((preset) => (
            <button key={preset.id} type="button" className="preset" onClick={() => props.onPreset(preset)} disabled={busy}>
              <strong>{preset.label}</strong>
              <span>{preset.hint}</span>
            </button>
          ))}
        </div>
      </section>

      <form
        className="panel-section"
        onSubmit={(event) => {
          event.preventDefault();
          props.onRun();
        }}
        noValidate
      >
        <h2>Configuration</h2>
        <div className="field-row wide-first">
          <Field label="Map" problem={problems["map"]}>
            <select value={form.map} onChange={(event) => set({ map: event.target.value })}>
              {props.maps.length === 0 && <option value={form.map}>{form.map || "—"}</option>}
              {props.maps.map((map) => (
                <option key={map.name} value={map.name}>
                  {map.name} · {map.nodes} nodes
                </option>
              ))}
            </select>
          </Field>
          <Field label="Dispatch policy" problem={problems["policy"]}>
            <select value={form.policy} onChange={(event) => set({ policy: event.target.value })}>
              {props.policies.length === 0 && <option value={form.policy}>{form.policy || "—"}</option>}
              {props.policies.map((policy) => (
                <option key={policy} value={policy}>
                  {policy}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div className="field-row">
          <Field label="Robots" problem={problems["robots"]} hint={range(limits?.robots)}>
            <input inputMode="numeric" value={form.robots} onChange={(event) => set({ robots: event.target.value })} />
          </Field>
          <Field label="Demand" unit="orders/h" problem={problems["orders_per_hour"]} hint={range(limits?.ordersPerHour)}>
            <input inputMode="decimal" value={form.ordersPerHour} onChange={(event) => set({ ordersPerHour: event.target.value })} />
          </Field>
        </div>
        <div className="field-row">
          <Field
            label="Order window"
            unit="s"
            problem={problems["duration_s"]}
            hint={Number.isFinite(seconds) && seconds >= 60 ? `orders arrive for ${duration(seconds)}` : range(limits?.durationS)}
          >
            <input inputMode="decimal" value={form.durationS} onChange={(event) => set({ durationS: event.target.value })} />
          </Field>
          <Field label="Seed" problem={problems["seed"]} hint="same seed, same run">
            <input inputMode="numeric" value={form.seed} onChange={(event) => set({ seed: event.target.value })} />
          </Field>
        </div>

        <label className="switch">
          <input type="checkbox" checked={form.traffic} onChange={(event) => set({ traffic: event.target.checked })} />
          <span>
            <strong>Traffic management</strong>
            <small>
              {form.traffic
                ? "Robots reserve nodes and corridors and plan around each other."
                : "Free flow: robots ignore each other. Delays, charging and breakdowns need traffic."}
            </small>
          </span>
        </label>

        <fieldset className="disturbances" disabled={!form.traffic}>
          <legend>Disturbances and charging</legend>
          <label className="check">
            <input
              type="checkbox"
              checked={form.traffic && form.delays.on}
              onChange={(event) => set({ delays: { ...form.delays, on: event.target.checked } })}
            />
            <span>Random delays while driving</span>
          </label>
          <DisturbanceFields
            name="delays"
            value={form.delays}
            problems={problems}
            disabled={!form.traffic}
            onChange={(delays) => set({ delays })}
          />
          <label className="check">
            <input
              type="checkbox"
              checked={form.traffic && form.breakdowns.on}
              onChange={(event) => set({ breakdowns: { ...form.breakdowns, on: event.target.checked } })}
            />
            <span>Breakdowns with repair times</span>
          </label>
          <DisturbanceFields
            name="breakdowns"
            value={form.breakdowns}
            problems={problems}
            disabled={!form.traffic}
            onChange={(breakdowns) => set({ breakdowns })}
          />
          {form.traffic && form.breakdowns.on && (
            <Field label="Recovery after a breakdown">
              <select value={form.recovery} onChange={(event) => set({ recovery: event.target.value })}>
                {(limits?.recovery ?? ["wait", "retime", "replan"]).map((name) => (
                  <option key={name} value={name}>
                    {RECOVERY_LABEL[name] ?? name}
                  </option>
                ))}
              </select>
            </Field>
          )}
          <Field label="Battery model" hint={form.charging !== "" ? "synthetic battery parameters" : undefined}>
            <select value={form.traffic ? form.charging : ""} onChange={(event) => set({ charging: event.target.value })}>
              <option value="">No battery model</option>
              {(limits?.charging ?? ["when-low", "opportunistic", "opportunistic-early"]).map((name) => (
                <option key={name} value={name}>
                  {CHARGING_LABEL[name] ?? name}
                </option>
              ))}
            </select>
          </Field>
        </fieldset>

        <button type="submit" className="primary" disabled={busy || invalid || service !== "up"}>
          {busy ? "Simulating…" : "Run simulation"}
        </button>
        <RunStatus {...props} />
      </form>

      <section className="panel-section">
        <h2>Recorded runs</h2>
        {props.recent.length === 0 ? (
          <p className="muted small">Runs made on this service appear here, newest first.</p>
        ) : (
          <ul className="recent">
            {props.recent.map((job) => {
              const loaded = job.id === props.loadedJobId;
              const canLoad = job.trace === "available" && !loaded;
              return (
                <li key={job.id} className={loaded ? "loaded" : ""}>
                  <div>
                    <span className="recent-title">{describe(job)}</span>
                    <span className={`recent-state ${job.status === "failed" ? "failed" : job.trace}`}>
                      {loaded ? "On screen" : job.status === "failed" ? "Failed" : (TRACE_NOTE[job.trace] ?? job.trace)}
                    </span>
                  </div>
                  {canLoad ? (
                    <button type="button" onClick={() => props.onLoadJob(job)} disabled={busy}>
                      Load
                    </button>
                  ) : (
                    (job.trace === "evicted" || job.trace === "too_large" || job.status === "failed") && (
                      <button type="button" onClick={() => props.onRerun(job)} disabled={busy || service !== "up"}>
                        Run again
                      </button>
                    )
                  )}
                </li>
              );
            })}
          </ul>
        )}
        <input
          ref={fileRef}
          type="file"
          accept=".json,application/json"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file !== undefined) props.onOpenFile(file);
            event.target.value = "";
          }}
        />
        <button type="button" className="secondary" onClick={() => fileRef.current?.click()} disabled={busy}>
          Open a trace file…
        </button>
        <p className="muted small">
          A file written by <code>amr-sim --trace</code>. It is read in this browser and sent nowhere.
        </p>
      </section>
    </div>
  );
}

function RunStatus({ phase, service, onRetryService, onRerun }: Props) {
  if (service === "down") {
    return (
      <div className="notice error" role="alert">
        <strong>The engine&apos;s service is not reachable.</strong>
        <span>
          Start it with <code>.venv\Scripts\amr-api.exe</code> in the repository folder. Trace files can still be opened.
        </span>
        <button type="button" onClick={onRetryService}>
          Try again
        </button>
      </div>
    );
  }
  switch (phase.kind) {
    case "idle":
      return null;
    case "submitting":
      return (
        <div className="notice" role="status">
          <span className="spinner" aria-hidden="true" />
          Sending the request…
        </div>
      );
    case "waiting":
      return (
        <div className="notice" role="status">
          <span className="spinner" aria-hidden="true" />
          {phase.job.status === "queued" ? "Queued behind other runs…" : "The engine is simulating the run…"}
        </div>
      );
    case "loading":
      return (
        <div className="notice" role="status">
          <span className="spinner" aria-hidden="true" />
          Loading the recorded run…
        </div>
      );
    case "failed":
      return (
        <div className="notice error" role="alert">
          <strong>{phase.title ?? "The run could not be made."}</strong>
          <span>{phase.message}</span>
        </div>
      );
    case "no-trace": {
      const text = {
        evicted:
          "The service keeps only the most recent traces, and dropped this one to make room. The KPIs of the run are still known; to play it back, run it again.",
        too_large: "This run's trace is larger than the service keeps, so it cannot be played back.",
        none: "This run was made without recording a trace, so there is nothing to play back.",
      }[phase.reason];
      return (
        <div className="notice warning" role="alert">
          <strong>No recorded run to play.</strong>
          <span>{text}</span>
          {phase.reason !== "too_large" && (
            <button type="button" onClick={() => onRerun(phase.job)}>
              Run again
            </button>
          )}
        </div>
      );
    }
  }
}
