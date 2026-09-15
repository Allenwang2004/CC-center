/**
 * Totals for the range. Bars are drawn as plain divs against a shared scale --
 * a chart library would be more machinery than four bar rows deserve.
 */

import { h, mount } from "../ui/dom.js";
import { basename, count, dayLabel, duration, plural } from "../core/format.js";
import { commitsIn, slices, store } from "../core/store.js";
import { renderSpend } from "./spend.js";

interface Bar {
  label: string;
  value: number;
  note?: string;
}

function bars(title: string, rows: Bar[], format: (n: number) => string): HTMLElement | null {
  if (!rows.length) return null;
  const top = Math.max(...rows.map((r) => r.value)) || 1;
  return h("section", { class: "block" },
    h("h4", null, title),
    h("div", { class: "bars" },
      rows.map((r) =>
        h("div", { class: "bar-row" },
          h("span", { class: "bar-label", title: r.label }, r.label),
          h("span", { class: "bar-track" },
            h("span", { class: "bar-fill", style: { width: `${(r.value / top) * 100}%` } })),
          h("span", { class: "bar-value" }, format(r.value)),
          r.note ? h("span", { class: "muted" }, r.note) : null))));
}

function statTile(value: string, label: string, note?: string): HTMLElement {
  return h("div", { class: "stat" },
    h("strong", null, value),
    h("span", { class: "stat-label" }, label),
    note ? h("span", { class: "muted" }, note) : null);
}

export function renderActivity(host: HTMLElement): void {
  // Spend comes first and ignores the range: it is the one long view in the tool.
  const spend = renderSpend();
  const items = slices();
  if (!items.length) {
    mount(host, spend, h("div", { class: "empty" },
      h("h3", null, "Nothing in this range"),
      h("p", null, "Pick a wider range in the sidebar.")));
    return;
  }

  const totals = items.reduce(
    (acc, it) => {
      acc.prompts += it.data.prompt_count;
      acc.active += it.data.active;
      acc.added += it.data.added;
      acc.removed += it.data.removed;
      acc.cost += it.data.cost ?? 0;
      acc.commits += commitsIn(it.data).length;
      for (const f of Object.keys(it.data.files)) acc.files.add(f);
      return acc;
    },
    { prompts: 0, active: 0, added: 0, removed: 0, cost: 0, commits: 0, files: new Set<string>() },
  );

  const perDay = new Map<string, number>();
  const perProject = new Map<string, number>();
  const perTool = new Map<string, number>();
  for (const it of items) {
    perDay.set(it.day, (perDay.get(it.day) ?? 0) + it.data.active);
    const p = basename(it.session.cwd);
    perProject.set(p, (perProject.get(p) ?? 0) + it.data.active);
    for (const [tool, n] of Object.entries(it.data.tools))
      perTool.set(tool, (perTool.get(tool) ?? 0) + n);
  }

  const rank = (m: Map<string, number>, limit = 10): Bar[] =>
    [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, limit)
      .map(([label, value]) => ({ label, value }));

  mount(host,
    spend,
    h("section", { class: "block-head" },
      h("h4", null, "In this range")),
    h("section", { class: "stat-row" },
      statTile(String(items.length), items.length === 1 ? "session" : "sessions"),
      statTile(String(totals.prompts),
               totals.prompts === 1 ? "question asked" : "questions asked"),
      statTile(duration(totals.active), "at the keyboard"),
      statTile(String(totals.files.size),
               totals.files.size === 1 ? "file touched" : "files touched",
        `+${count(totals.added)} / -${count(totals.removed)}`),
      statTile(String(totals.commits), totals.commits === 1 ? "commit" : "commits"),
      totals.cost ? statTile(`$${totals.cost.toFixed(2)}`, "spent") : null),
    h("div", { class: "activity-grid" },
      bars("Time by day",
        [...perDay.entries()].sort((a, b) => a[0].localeCompare(b[0]))
          .map(([label, value]) => ({ label: dayLabel(label), value })),
        duration),
      bars("Time by project", rank(perProject), duration),
      bars("Tools used", rank(perTool), (n) => String(n))));
}

export function renderReport(host: HTMLElement): void {
  const view = h("div", { class: "prose" });
  const raw = h("pre", { class: "raw", hidden: !store.showRaw });
  raw.textContent = store.reportText;
  view.innerHTML = markdownToHtml(store.reportText);
  view.hidden = store.showRaw;
  mount(host, view, raw);
  const meta = document.getElementById("report-meta");
  if (meta)
    meta.textContent =
      `${plural(store.report?.sessions.length ?? 0, "session")}`
      + ` / ${store.reportText.length.toLocaleString()} characters`;
}

/** Enough Markdown for the report the CLI already produces. */
export function markdownToHtml(md: string): string {
  const escape = (s: string) =>
    s.replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);
  const inline = (t: string) =>
    escape(t)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g,
        '<a href="$2" target="_blank" rel="noreferrer">$1</a>');

  const out: string[] = [];
  let depth = 0;
  const closeTo = (n: number) => {
    while (depth > n) {
      out.push("</ul>");
      depth--;
    }
  };
  for (const rawLine of md.split("\n")) {
    const line = rawLine.replace(/\s+$/, "");
    const bullet = /^(\s*)-\s+(.*)$/.exec(line);
    if (bullet) {
      const want = Math.floor((bullet[1] as string).length / 2) + 1;
      while (depth < want) {
        out.push("<ul>");
        depth++;
      }
      closeTo(want);
      out.push(`<li>${inline(bullet[2] as string)}</li>`);
      continue;
    }
    closeTo(0);
    if (!line.trim()) continue;
    if (line.startsWith("### ")) out.push(`<h3>${inline(line.slice(4))}</h3>`);
    else if (line.startsWith("## ")) out.push(`<h2>${inline(line.slice(3))}</h2>`);
    else if (line.startsWith("# ")) out.push(`<h1>${inline(line.slice(2))}</h1>`);
    else if (/^---+$/.test(line)) out.push("<hr>");
    else out.push(`<p>${inline(line)}</p>`);
  }
  closeTo(0);
  return out.join("\n");
}
