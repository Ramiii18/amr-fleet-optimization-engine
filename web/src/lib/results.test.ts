import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { ResultsFormatError, along, byKpi, parseResults, scaleOf, signed } from "./results";

const FOLDER = new URL("../../../docs/results/", import.meta.url);

function published(file: string): Record<string, unknown> {
  return JSON.parse(readFileSync(fileURLToPath(new URL(file, FOLDER)), "utf8"));
}

/** A published file with one change, made on a copy. */
interface Loose {
  format: unknown;
  version: unknown;
  experiment: Record<string, unknown>;
  rows: [Record<string, unknown>, Record<string, unknown>, Record<string, unknown>];
  analysis: [Record<string, unknown>];
}

function at<T>(items: readonly T[], index: number): T {
  const item = items[index];
  if (item === undefined) throw new Error(`no item ${index}`);
  return item;
}

function changed(edit: (root: Loose) => void): unknown {
  const root = structuredClone(published("lab_dispatch_schedule.json")) as unknown as Loose;
  edit(root);
  return root;
}

describe("experiment results", () => {
  it.each([
    "lab_dispatch_schedule.json",
    "lab_dispatch_independent.json",
    "lab_dispatch_moderate_schedule.json",
    "lab_dispatch_moderate_independent.json",
  ])("reads the published file %s", (file) => {
    const results = parseResults(published(file));
    expect(results.seeds).toBe(20);
    expect(results.runs).toBe(80);
    expect(results.baseline).toBe("greedy");
    expect(results.contrasts).toHaveLength(6);
    expect(results.model).toBe(file.includes("independent") ? "independent" : "schedule");
  });

  it("keeps the lab's figures as written", () => {
    const results = parseResults(published("lab_dispatch_schedule.json"));
    const first = at(results.contrasts, 0);
    expect(first.family).toBe("medium-8");
    expect(first.policy).toBe("insertion-fair");
    expect(first.difference).toBeCloseTo(-224.548, 3);
    expect(first.low).toBeCloseTo(-411.111, 3);
    expect(first.high).toBeCloseTo(-37.986, 3);
    expect([first.wins, first.losses, first.ties]).toEqual([13, 7, 0]);
    expect(first.verdict).toBe("better");
  });

  it("puts the primary KPI first", () => {
    const groups = byKpi(parseResults(published("lab_dispatch_schedule.json")));
    expect(groups.map((group) => group.kpi)).toEqual(["completion_mean_s", "completion_p95_s", "completion_max_s"]);
    expect(at(groups, 0).primary).toBe(true);
    expect(at(groups, 0).contrasts).toHaveLength(2);
  });

  it.each<[string, (root: Loose) => void]>([
    ["another format", (root) => (root.format = "amr-replay-trace")],
    ["a later version", (root) => (root.version = 2)],
    ["an unknown verdict", (root) => (root.analysis[0].verdict = "significant")],
    ["a difference outside its interval", (root) => (root.analysis[0].high = -300)],
    ["a one-ended interval", (root) => (root.analysis[0].low = null)],
    ["wins that do not add up", (root) => (root.analysis[0].wins = 14)],
    ["a difference that is not the means' difference", (root) => (root.analysis[0].mean_baseline = 1300)],
    ["an unknown family", (root) => (root.analysis[0].variant = "small-3/insertion-fair")],
    ["an unknown policy", (root) => (root.analysis[0].variant = "medium-8/hungarian")],
    ["a missing run", (root) => void (root.rows as unknown[]).pop()],
    ["a baseline outside the policies", (root) => (root.experiment.baseline = "cpsat")],
    ["a text where a number belongs", (root) => (root.analysis[0].difference = "-224.5")],
  ])("refuses %s", (_name, edit) => {
    expect(() => parseResults(changed(edit))).toThrow(ResultsFormatError);
  });

  it("refuses what is not an object", () => {
    expect(() => parseResults([])).toThrow(ResultsFormatError);
    expect(() => parseResults(null)).toThrow(ResultsFormatError);
  });

  it("accepts a comparison whose interval is not defined", () => {
    const results = parseResults(
      changed((root) => {
        root.analysis[0].low = null;
        root.analysis[0].high = null;
        root.analysis[0].correlation = null;
      }),
    );
    expect(at(results.contrasts, 0).low).toBeNull();
    expect(at(results.contrasts, 0).correlation).toBeNull();
  });

  it("counts the runs that left orders or outlasted their schedule", () => {
    const results = parseResults(
      changed((root) => {
        root.rows[0].all_delivered = false;
        root.rows[1].beyond_horizon = true;
        root.rows[2].beyond_horizon = true;
      }),
    );
    const before = parseResults(published("lab_dispatch_schedule.json"));
    expect(results.undelivered).toBe(before.undelivered + 1);
    expect(results.beyondHorizon).toBe(before.beyondHorizon + 2);
  });
});

describe("the interval axis", () => {
  const results = parseResults(published("lab_dispatch_schedule.json"));
  const group = at(byKpi(results), 0);
  const scale = scaleOf(group.contrasts, results.margin);

  it("holds every interval, zero and the margin", () => {
    for (const contrast of group.contrasts) {
      expect(along(contrast.low ?? 0, scale)).toBeGreaterThan(0);
      expect(along(contrast.high ?? 0, scale)).toBeLessThan(1);
    }
    for (const value of [0, -results.margin, results.margin]) {
      expect(along(value, scale)).toBeGreaterThan(0);
      expect(along(value, scale)).toBeLessThan(1);
    }
  });

  it("keeps the order of values", () => {
    expect(along(-100, scale)).toBeLessThan(along(0, scale));
    expect(along(scale.min, scale)).toBe(0);
    expect(along(scale.max, scale)).toBe(1);
  });

  it("is never empty", () => {
    const flat = scaleOf([], 0);
    expect(flat.max).toBeGreaterThan(flat.min);
  });
});

describe("signed figures", () => {
  it("shows the sign of a difference", () => {
    expect(signed(12.34)).toBe("+12.3");
    expect(signed(-12.34)).toBe("−12.3");
    expect(signed(0)).toBe("0.0");
    expect(signed(0.04)).toBe("0.0");
  });
});
