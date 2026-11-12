/** Text for numbers and times. A value that is null (an undefined KPI) is a dash, never a zero. */

export const DASH = "—";

/** Simulated time as `m:ss.s` (or `h:mm:ss.s`), from seconds. */
export function clock(seconds: number, decimals: 0 | 1 = 1): string {
  const safe = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
  const step = decimals === 1 ? 10 : 1;
  const units = Math.floor(safe * step + 1e-6); // tenths (or whole seconds), never rounded up
  const whole = Math.floor(units / step);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = whole % 60;
  const tail = decimals === 1 ? `.${units % 10}` : "";
  const mmss = `${String(secs).padStart(2, "0")}${tail}`;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${mmss}`
    : `${minutes}:${mmss}`;
}

/** A duration in words: `42.0 s`, `3 min 05 s`, `1 h 02 min`. */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return DASH;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const whole = Math.round(seconds);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = whole % 60;
  if (hours > 0) return `${hours} h ${String(minutes).padStart(2, "0")} min`;
  return `${minutes} min ${String(secs).padStart(2, "0")} s`;
}

export function number(value: unknown, decimals = 0): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return DASH;
  return value.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function percent(value: unknown, decimals = 1): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return DASH;
  return `${(value * 100).toFixed(decimals)}%`;
}

export function metres(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return DASH;
  return value >= 10_000 ? `${(value / 1000).toFixed(1)} km` : `${number(value, 0)} m`;
}

export function bytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(0)} kB`;
  return `${(value / 1024 / 1024).toFixed(2)} MB`;
}
