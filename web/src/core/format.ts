/** Formatting for values the tool computed. Everything here ends up in mono. */

export function duration(seconds: number | null | undefined): string {
  const s = Math.round(seconds ?? 0);
  if (s < 60) return `${s}s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

export function count(n: number | null | undefined): string {
  const v = n ?? 0;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e4) return `${Math.round(v / 1e3)}k`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
  return String(v);
}

/** "$0.42", "$153.06", "$1,234" -- cents matter until the dollars do. */
export function money(n: number | null | undefined): string {
  const v = n ?? 0;
  if (v >= 1000) return `$${Math.round(v).toLocaleString("en-US")}`;
  return `$${v.toFixed(2)}`;
}

export function ago(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !isFinite(seconds)) return "—";
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} d ago`;
}

export const clock = (iso: string | null | undefined): string =>
  iso
    ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })
    : "--:--";

export const epoch = (iso: string | null | undefined): number =>
  iso ? new Date(iso).getTime() / 1000 : 0;

const WEEKDAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Wed 9 Sep" — a log reads better with the weekday first. */
export function dayLabel(day: string): string {
  const d = new Date(day + "T00:00:00");
  if (isNaN(d.getTime())) return day;
  return `${WEEKDAY[d.getDay()]} ${d.getDate()} ${MONTH[d.getMonth()]}`;
}

/** "Tue 14:00" — for a moment that is more than a day out, the weekday is what you want. */
export function whenLabel(epochSeconds: number): string {
  const d = new Date(epochSeconds * 1000);
  if (isNaN(d.getTime())) return "";
  return `${WEEKDAY[d.getDay()]} ${clock(d.toISOString())}`;
}

export const isToday = (day: string): boolean => day === todayKey();

export function todayKey(): string {
  const d = new Date();
  return [d.getFullYear(), d.getMonth() + 1, d.getDate()]
    .map((n, i) => (i ? String(n).padStart(2, "0") : String(n)))
    .join("-");
}

/** note refs are YYYY-MM-DD-HHMM[-n]; show them as a time of day. */
export const refClock = (ref: string): string => `${ref.slice(11, 13)}:${ref.slice(13, 15)}`;

export const basename = (path: string | null | undefined): string =>
  (path || "").split("/").filter(Boolean).pop() || path || "unknown";

export const relPath = (file: string, cwd: string | null): string =>
  cwd && file.startsWith(cwd + "/") ? file.slice(cwd.length + 1) : file;

/** Inline scripts are most of a command's length and none of its meaning. */
export function shortCommand(cmd: string, limit = 72): string {
  for (const marker of ["<<", " -c ", " -e "]) {
    const i = cmd.indexOf(marker);
    if (i > 0) return cmd.slice(0, i).trim() + " …inline script";
  }
  // Commands arrive as they were run, newlines and all; the closed row is one line.
  const flat = cmd.replace(/\s+/g, " ").trim();
  return flat.length > limit ? flat.slice(0, limit) + "…" : flat;
}

export const firstLine = (text: string, limit = 90): string => {
  const line = (text || "").split("\n").find((l) => l.trim()) || "";
  return line.length > limit ? line.slice(0, limit) + "…" : line;
};

/**
 * "1 session", "2 sessions". Counts are everywhere in this UI and a stray
 * "1 projects" makes the whole column look machine-generated.
 */
export const plural = (n: number, one: string, many = `${one}s`): string =>
  `${count(n)} ${n === 1 ? one : many}`;
