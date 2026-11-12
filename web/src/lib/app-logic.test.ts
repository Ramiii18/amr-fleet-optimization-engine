import { describe, expect, it } from "vitest";

import { errorDetail, limitProblem, limitsFromOpenApi } from "./api";
import { bytes, clock, duration, metres, number, percent } from "./format";
import { Playback } from "./playback";
import { PRESETS, applyPreset, defaultForm, formOfRequest, validateForm } from "./runForm";

// The schemas of `POST /simulations`, as the service publishes them at /openapi.json. A Python
// test (`test_the_api_documentation_gives_the_form_its_limits`) checks that the service still
// publishes these values.
const OPEN_API = {
  components: {
    schemas: {
      SimulationRequest: {
        properties: {
          map: { type: "string", default: "large" },
          policy: { type: "string", default: "greedy" },
          robots: { type: "integer", maximum: 50, minimum: 1, default: 5 },
          orders_per_hour: { type: "number", maximum: 1000, exclusiveMinimum: 0, default: 60 },
          duration_s: { type: "number", maximum: 7200, exclusiveMinimum: 0, default: 3600 },
          seed: { type: "integer", maximum: 2147483647, minimum: 0, default: 0 },
          traffic: { type: "boolean", default: true },
          delays: { anyOf: [{ $ref: "#/components/schemas/Disturbance" }, { type: "null" }] },
          charging: {
            anyOf: [
              { type: "string", enum: ["when-low", "opportunistic", "opportunistic-early"] },
              { type: "null" },
            ],
          },
          breakdowns: { anyOf: [{ $ref: "#/components/schemas/Disturbance" }, { type: "null" }] },
          recovery: { type: "string", enum: ["wait", "retime", "replan"], default: "wait" },
          record_trace: { type: "boolean", default: false },
        },
      },
      Disturbance: {
        properties: {
          share: { type: "number", maximum: 1, exclusiveMinimum: 0 },
          min_s: { type: "number", maximum: 600, exclusiveMinimum: 0 },
          max_s: { type: "number", maximum: 600, exclusiveMinimum: 0 },
        },
      },
    },
  },
};
const LIMITS = limitsFromOpenApi(OPEN_API);
const MAPS = ["small", "medium", "large"];
const POLICIES = ["cpsat-fair", "greedy", "insertion-fair"];

describe("limitsFromOpenApi", () => {
  it("reads every limit and choice of a simulation request", () => {
    expect(LIMITS).not.toBeNull();
    expect(LIMITS?.robots).toEqual({ min: 1, exclusive: false, max: 50, fallback: 5, integer: true });
    expect(LIMITS?.ordersPerHour).toMatchObject({ min: 0, exclusive: true, max: 1000 });
    expect(LIMITS?.durationS).toMatchObject({ max: 7200, fallback: 3600 });
    expect(LIMITS?.share).toMatchObject({ min: 0, exclusive: true, max: 1 });
    expect(LIMITS?.disturbanceS.max).toBe(600);
    expect(LIMITS?.charging).toEqual(["when-low", "opportunistic", "opportunistic-early"]);
    expect(LIMITS?.recovery).toEqual(["wait", "retime", "replan"]);
    expect(LIMITS?.defaults).toEqual({ map: "large", policy: "greedy", traffic: true, recovery: "wait" });
  });

  it("gives up on a document of another shape, and leaves validation to the service", () => {
    for (const document of [null, {}, { components: { schemas: {} } }, "openapi"]) {
      expect(limitsFromOpenApi(document)).toBeNull();
    }
  });

  it("explains a value outside a limit", () => {
    const robots = LIMITS?.robots;
    const rate = LIMITS?.ordersPerHour;
    if (robots === undefined || rate === undefined) throw new Error("no limits");
    expect(limitProblem(1, robots)).toBeNull();
    expect(limitProblem(50, robots)).toBeNull();
    expect(limitProblem(0, robots)).toBe("Must be at least 1.");
    expect(limitProblem(51, robots)).toBe("Must be at most 50.");
    expect(limitProblem(2.5, robots)).toBe("Enter a whole number.");
    expect(limitProblem(0, rate)).toBe("Must be greater than 0.");
    expect(limitProblem(0.5, rate)).toBeNull();
    expect(limitProblem(Number.NaN, rate)).toBe("Enter a number.");
  });
});

describe("the run form", () => {
  it("starts from the service's defaults and always asks for a trace", () => {
    const { request, problems } = validateForm(defaultForm(LIMITS, MAPS, POLICIES), LIMITS);
    expect(problems).toEqual({});
    expect(request).toEqual({
      map: "large",
      policy: "greedy",
      robots: 5,
      orders_per_hour: 60,
      duration_s: 3600,
      seed: 0,
      traffic: true,
      delays: null,
      charging: null,
      breakdowns: null,
      recovery: "wait",
      record_trace: true,
    });
  });

  it("reports each value the service would refuse, and sends nothing", () => {
    const form = {
      ...defaultForm(LIMITS, MAPS, POLICIES),
      robots: "51",
      ordersPerHour: "0",
      durationS: "abc",
      seed: "-1",
      delays: { on: true, sharePercent: "150", minS: "5", maxS: "2" },
    };
    const { request, problems } = validateForm(form, LIMITS);
    expect(request).toBeNull();
    expect(problems).toEqual({
      robots: "Must be at most 50.",
      orders_per_hour: "Must be greater than 0.",
      duration_s: "Enter a number.",
      seed: "Must be at least 0.",
      "delays.share": "Must be at most 100.",
      "delays.max_s": "Must be at least the minimum.",
    });
  });

  it("sends disturbances as shares, and a recovery policy only with breakdowns", () => {
    const base = defaultForm(LIMITS, MAPS, POLICIES);
    const form = {
      ...base,
      delays: { on: true, sharePercent: "12.5", minS: "1", maxS: "5" },
      charging: "opportunistic",
      breakdowns: { on: true, sharePercent: "2", minS: "30", maxS: "180" },
      recovery: "replan",
    };
    const { request } = validateForm(form, LIMITS);
    expect(request?.delays).toEqual({ share: 0.125, min_s: 1, max_s: 5 });
    expect(request?.breakdowns).toEqual({ share: 0.02, min_s: 30, max_s: 180 });
    expect([request?.charging, request?.recovery]).toEqual(["opportunistic", "replan"]);

    const without = validateForm({ ...form, breakdowns: { ...form.breakdowns, on: false } }, LIMITS);
    expect([without.request?.breakdowns, without.request?.recovery]).toEqual([null, "wait"]);
  });

  it("switches off what needs traffic when traffic is off", () => {
    const form = {
      ...defaultForm(LIMITS, MAPS, POLICIES),
      traffic: false,
      delays: { on: true, sharePercent: "10", minS: "1", maxS: "5" },
      charging: "when-low",
      breakdowns: { on: true, sharePercent: "2", minS: "30", maxS: "180" },
      recovery: "replan",
    };
    const { request, problems } = validateForm(form, LIMITS);
    expect(problems).toEqual({});
    expect(request).toMatchObject({
      traffic: false,
      delays: null,
      charging: null,
      breakdowns: null,
      recovery: "wait",
    });
  });

  it("still builds a request when the limits are unknown", () => {
    const { request, problems } = validateForm(defaultForm(null, MAPS, POLICIES), null);
    expect(problems).toEqual({});
    expect(request).toMatchObject({ map: "large", robots: 5, record_trace: true });
  });

  it.each(PRESETS)("has a preset $id that the service's limits accept", (preset) => {
    const form = applyPreset(preset, LIMITS, MAPS, POLICIES);
    const { request, problems } = validateForm(form, LIMITS);
    expect(problems).toEqual({});
    expect(MAPS).toContain(request?.map);
    expect(POLICIES).toContain(request?.policy);
  });

  it("replaces a preset's map or policy that the service does not offer", () => {
    const fleet = PRESETS.find((preset) => preset.id === "fleet");
    if (fleet === undefined) throw new Error("no preset");
    const form = applyPreset(fleet, LIMITS, ["tiny"], ["greedy"]);
    expect([form.map, form.policy]).toEqual(["tiny", "greedy"]);
  });

  it("turns a job's request back into the form that made it", () => {
    const base = defaultForm(LIMITS, MAPS, POLICIES);
    const form = {
      ...base,
      map: "small",
      robots: "7",
      delays: { on: true, sharePercent: "12.5", minS: "1", maxS: "5" },
      breakdowns: { on: true, sharePercent: "2", minS: "30", maxS: "180" },
      recovery: "retime",
      charging: "when-low",
    };
    const { request } = validateForm(form, LIMITS);
    if (request === null) throw new Error("no request");
    expect(formOfRequest(request, base)).toEqual(form);
    expect(validateForm(formOfRequest(request, base), LIMITS).request).toEqual(request);
  });
});

describe("errorDetail", () => {
  it("reads the reason of a refusal", () => {
    expect(errorDetail({ detail: "too many simulations queued or running" })).toBe(
      "too many simulations queued or running",
    );
  });

  it("reads the field problems of an invalid request", () => {
    const body = {
      detail: [
        { loc: ["body", "robots"], msg: "Input should be less than or equal to 50", type: "x" },
        { loc: ["body"], msg: "Value error, delays need traffic", type: "value_error" },
      ],
    };
    expect(errorDetail(body)).toBe(
      "robots: Input should be less than or equal to 50; delays need traffic",
    );
  });

  it("has nothing to say about another body", () => {
    for (const body of [null, "Bad Gateway", {}, { detail: 3 }]) expect(errorDetail(body)).toBeNull();
  });
});

describe("Playback", () => {
  const TICK_S = 0.1;

  it("advances by simulated time, at the chosen speed", () => {
    const playback = new Playback(1000, TICK_S);
    playback.setSpeed(10);
    playback.play();
    playback.advance(500); // half a real second at 10x: 5 simulated seconds, 50 ticks
    expect(playback.tick).toBeCloseTo(50, 9);
    expect(playback.seconds).toBeCloseTo(5, 9);
    playback.advance(16.7); // one frame: a fraction of a tick
    expect(playback.tick).toBeCloseTo(51.67, 9);
    expect(Number.isInteger(playback.tick)).toBe(false);
  });

  it("does not move while paused", () => {
    const playback = new Playback(1000, TICK_S);
    playback.advance(1000);
    expect(playback.tick).toBe(0);
  });

  it("stops exactly at the end of the run, and plays again from the start", () => {
    const playback = new Playback(100, TICK_S);
    playback.setSpeed(120);
    playback.play();
    playback.advance(60_000);
    expect([playback.tick, playback.playing, playback.atEnd]).toEqual([100, false, true]);
    playback.play();
    expect([playback.tick, playback.playing]).toEqual([0, true]);
  });

  it("keeps the playhead inside the run when seeking", () => {
    const playback = new Playback(100, TICK_S);
    for (const [target, expected] of [
      [-5, 0],
      [42.5, 42.5],
      [100, 100],
      [5000, 100],
      [Number.NaN, 0],
    ] as const) {
      playback.seek(target);
      expect(playback.tick).toBe(expected);
    }
  });

  it("steps by whole ticks from a fractional position, and pauses", () => {
    const playback = new Playback(100, TICK_S);
    playback.seek(42.3);
    playback.play();
    playback.stepTicks(1);
    expect([playback.tick, playback.playing]).toEqual([43, false]);
    playback.seek(42.3);
    playback.stepTicks(-1);
    expect(playback.tick).toBe(42);
    playback.stepSeconds(1);
    expect(playback.tick).toBe(52);
    playback.stepSeconds(-60);
    expect(playback.tick).toBe(0);
    playback.stepTicks(1e6);
    expect(playback.tick).toBe(100);
  });

  it("restarts paused at tick 0, and tells its listeners about every change", () => {
    const playback = new Playback(100, TICK_S);
    let calls = 0;
    const stop = playback.subscribe(() => (calls += 1));
    playback.seek(10);
    playback.seek(10); // no change, no call
    playback.play();
    playback.restart();
    expect([playback.tick, playback.playing, calls]).toEqual([0, false, 3]);
    stop();
    playback.seek(20);
    expect(calls).toBe(3);
  });

  it("cannot play a run of no length", () => {
    const playback = new Playback(0, TICK_S);
    playback.play();
    expect(playback.playing).toBe(false);
  });
});

describe("formatting", () => {
  it("writes simulated time without rounding up", () => {
    expect(clock(0)).toBe("0:00.0");
    expect(clock(59.99)).toBe("0:59.9");
    expect(clock(61.25)).toBe("1:01.2");
    expect(clock(3723.4)).toBe("1:02:03.4");
    expect(clock(653.8, 0)).toBe("10:53");
    expect(clock(-1)).toBe("0:00.0");
    expect(clock(0.3)).toBe("0:00.3"); // 0.3 is stored a little below 0.3
  });

  it("writes an undefined value as a dash, never as zero", () => {
    expect([number(null), percent(null), metres(undefined), duration(null)]).toEqual([
      "—",
      "—",
      "—",
      "—",
    ]);
    expect(number(Number.NaN)).toBe("—");
  });

  it("writes numbers with their units", () => {
    expect(number(1234.56, 1)).toBe("1,234.6");
    expect(percent(0.4567)).toBe("45.7%");
    expect(metres(812.4)).toBe("812 m");
    expect(metres(12_345)).toBe("12.3 km");
    expect(duration(42)).toBe("42.0 s");
    expect(duration(185)).toBe("3 min 05 s");
    expect(duration(3725)).toBe("1 h 02 min");
    expect(bytes(900)).toBe("900 B");
    expect(bytes(329_758)).toBe("322 kB");
    expect(bytes(1_479_567)).toBe("1.41 MB");
  });
});
