/**
 * What every day cost, at API list prices, for as long as there are transcripts.
 *
 * This is the one view that ignores the range in the sidebar. A calendar of
 * weeks, one cell a day, coloured by spend; hover a cell and the line beneath
 * reads it out; the table under that lists every day for anyone who wants the
 * numbers rather than the shape. Counting is the scanner's (`daily_totals`),
 * so the CLI and this page cannot disagree.
 */

import { h } from "../ui/dom.js";
import { count, dayLabel, money, todayKey } from "../core/format.js";
import { store } from "../core/store.js";
import type { DailyTotal } from "../core/types.js";

const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/*
 * The calendar always fills the pane and always shows a year: 53 columns of
 * whatever a fifty-third of the width comes to, so the cells stay small
 * enough to read as a texture and the grid is the same shape on any screen.
 */
const MAX_WEEKS = 53;
const MIN_WEEKS = 53;

const key = (d: Date): string =>
  [d.getFullYear(), d.getMonth() + 1, d.getDate()]
    .map((n, i) => (i ? String(n).padStart(2, "0") : String(n))).join("-");

const parse = (day: string): Date => new Date(day + "T00:00:00");

/** Monday on or before this date -- weeks run Mon..Sun so a weekend is one block. */
function mondayOf(d: Date): Date {
  const out = new Date(d);
  out.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return out;
}

/**
 * Four steps by quartile of the non-zero days, so one enormous day does not
 * wash every other day out to the palest step. Fewer than four distinct values
 * and it falls back to plain fractions of the largest.
 */
function leveller(values: number[]): (cost: number) => number {
  const sorted = [...new Set(values.filter((v) => v > 0))].sort((a, b) => a - b);
  if (!sorted.length) return () => 0;
  const at = (q: number) => sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * q))]!;
  const cuts = sorted.length >= 4
    ? [at(0.25), at(0.5), at(0.75)]
    : [0.25, 0.5, 0.75].map((f) => f * sorted[sorted.length - 1]!);
  return (cost) => {
    if (cost <= 0) return 0;
    if (cost <= cuts[0]!) return 1;
    if (cost <= cuts[1]!) return 2;
    if (cost <= cuts[2]!) return 3;
    return 4;
  };
}

function tokens(d: DailyTotal): number {
  return d.in + d.out + d.cache_read + d.cache_write;
}

function readout(day: string, d: DailyTotal | undefined): HTMLElement {
  if (!d) return h("p", { class: "spend-readout" },
    h("strong", null, dayLabel(day)), h("span", { class: "muted" }, "nothing ran"));
  const models = Object.entries(d.models).sort((a, b) => b[1] - a[1]);
  const hosts = Object.entries(d.hosts).filter(([host]) => host !== store.localHost);
  return h("p", { class: "spend-readout" },
    h("strong", null, dayLabel(day)),
    h("span", { class: "spend-cost" }, money(d.cost)),
    h("span", { class: "muted" }, `${count(d.calls)} calls`),
    h("span", { class: "muted" },
      `in ${count(d.in)} · out ${count(d.out)} · cache read ${count(d.cache_read)} · cache write ${count(d.cache_write)}`),
    models.length > 1
      ? h("span", { class: "muted" }, models.map(([m, c]) => `${m} ${money(c)}`).join(", "))
      : models[0] ? h("span", { class: "muted" }, models[0][0]) : null,
    hosts.length
      ? h("span", { class: "muted" }, hosts.map(([host, c]) => `${host} ${money(c)}`).join(", "))
      : null);
}

function table(days: string[], daily: Record<string, DailyTotal>): HTMLElement {
  const cell = (text: string, cls = "") => h("td", { class: cls }, text);
  return h("details", { class: "spend-table" },
    h("summary", null, `Every day, ${days.length} of them`),
    h("div", { class: "table-scroll" },
      h("table", null,
        h("thead", null, h("tr", null,
          h("th", null, "Day"), h("th", { class: "num" }, "Calls"),
          h("th", { class: "num" }, "Input"), h("th", { class: "num" }, "Output"),
          h("th", { class: "num" }, "Cache read"), h("th", { class: "num" }, "Cache write"),
          h("th", { class: "num" }, "Cost"))),
        h("tbody", null,
          [...days].reverse().map((day) => {
            const d = daily[day]!;
            return h("tr", null,
              cell(dayLabel(day)), cell(count(d.calls), "num"),
              cell(count(d.in), "num"), cell(count(d.out), "num"),
              cell(count(d.cache_read), "num"), cell(count(d.cache_write), "num"),
              cell(money(d.cost), "num cost"));
          })))));
}

export function renderSpend(): HTMLElement | null {
  const daily = store.report?.daily ?? {};
  const days = Object.keys(daily).sort();
  if (!days.length) return null;

  const today = parse(todayKey());
  const first = parse(days[0]!);
  const lastMonday = mondayOf(today);
  const weeksBack = Math.round((lastMonday.getTime() - mondayOf(first).getTime()) / 604800000) + 1;
  const weeks = Math.max(MIN_WEEKS, Math.min(MAX_WEEKS, weeksBack));
  const start = new Date(lastMonday);
  start.setDate(lastMonday.getDate() - (weeks - 1) * 7);

  const level = leveller(days.map((k) => daily[k]!.cost));
  const total = days.reduce((a, k) => a + daily[k]!.cost, 0);
  const totalTokens = days.reduce((a, k) => a + tokens(daily[k]!), 0);
  const busiest = days.reduce((a, k) => (daily[k]!.cost > (daily[a]?.cost ?? 0) ? k : a), days[0]!);

  const detail = h("div", { class: "spend-detail" });
  const latest = days[days.length - 1]!;
  const show = (day: string) => detail.replaceChildren(readout(day, daily[day]));
  show(latest);

  // One grid for the weekday labels and the cells: the labels are pinned to
  // column 1, the cells flow down each week after them, so the rows line up.
  const columns = `28px repeat(${weeks}, minmax(0, 1fr))`;
  const cells: HTMLElement[] = ["Mon", "", "Wed", "", "Fri", "", ""].map((t, i) =>
    h("span", { class: "spend-day", style: { gridColumn: "1", gridRow: String(i + 1) } }, t));
  const months: HTMLElement[] = [];
  let lastMonth = -1;
  for (let w = 0; w < weeks; w++) {
    for (let i = 0; i < 7; i++) {
      const d = new Date(start);
      d.setDate(start.getDate() + w * 7 + i);
      const k = key(d);
      const future = d > today;
      const before = d < first;
      const t = daily[k];
      if (i === 0 && d.getMonth() !== lastMonth && (w === 0 || d.getDate() <= 7)) {
        lastMonth = d.getMonth();
        months.push(h("span", { class: "spend-month", style: { gridColumn: String(w + 2) } },
          MONTH[d.getMonth()]));
      }
      if (future) {
        cells.push(h("span", { class: "spend-cell void" }));
        continue;
      }
      if (before) {
        // Older than the oldest transcript: not "nothing ran", just no record.
        cells.push(h("span", { class: "spend-cell l0", title: `${dayLabel(k)} · before the first transcript on record` }));
        continue;
      }
      const btn = h("button", {
        class: `spend-cell l${level(t?.cost ?? 0)}`, type: "button",
        title: `${dayLabel(k)} · ${t ? money(t.cost) : "nothing ran"}`,
        data: { day: k },
        aria: { label: `${dayLabel(k)}: ${t ? money(t.cost) : "nothing ran"}` },
      }) as HTMLButtonElement;
      const peek = () => show(k);
      btn.addEventListener("mouseenter", peek);
      btn.addEventListener("focus", peek);
      cells.push(btn);
    }
  }
  const grid = h("div", { class: "spend-grid", style: { gridTemplateColumns: columns } }, cells);
  grid.addEventListener("mouseleave", () => show(latest));

  return h("section", { class: "block spend" },
    h("h4", null, "Spend",
      h("span", { class: "muted" },
        `at API list prices · every day on record, whatever the range · since ${dayLabel(days[0]!)}`)),
    h("p", { class: "spend-summary" },
      h("strong", { class: "spend-cost" }, money(total)),
      h("span", { class: "muted" }, ` across ${days.length} days · ${count(totalTokens)} tokens`
        + ` · busiest ${dayLabel(busiest)} at ${money(daily[busiest]!.cost)}`)),
    h("div", { class: "spend-calendar" },
      h("div", { class: "spend-months", style: { gridTemplateColumns: columns } }, months),
      grid,
      h("div", { class: "spend-legend" },
        h("span", { class: "muted" }, "less"),
        [0, 1, 2, 3, 4].map((l) => h("span", { class: `spend-cell l${l}` })),
        h("span", { class: "muted" }, "more"))),
    detail,
    table(days, daily));
}
