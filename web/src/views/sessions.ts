/** Raw sessions, grouped by day then project. The detail behind the ledger. */

import { h, mount, sticky } from "../ui/dom.js";
import {
  basename, clock, count, dayLabel, duration, plural, relPath,
} from "../core/format.js";
import { changesFor, commitsIn, slices, store, type Slice } from "../core/store.js";
import { changeSummary, turnRow } from "./detail.js";

function keep(slice: Slice): boolean {
  const f = store.filters;
  if (f.host && slice.session.host !== f.host) return false;
  if (f.project && basename(slice.session.cwd) !== f.project) return false;
  if (f.changedOnly && !Object.keys(slice.data.files).length) return false;
  const q = f.query.trim().toLowerCase();
  if (!q) return true;
  return [
    slice.session.title,
    ...slice.data.prompts,
    ...Object.keys(slice.data.files),
    ...slice.data.commands,
  ].join(" ").toLowerCase().includes(q);
}

function field(title: string, body: Node | null): HTMLElement | null {
  return body ? h("div", { class: "field" }, h("h5", null, title), body) : null;
}

function resume(host: string, cwd: string | null, id: string): string {
  const cd = `cd ${cwd ?? "."} && claude --resume ${id}`;
  return host === store.localHost ? cd : `ssh -t ${host} '${cd}'`;
}

function card(slice: Slice): HTMLElement {
  const { session: s, data: d } = slice;
  const files = Object.entries(d.files).sort((a, b) => b[1] - a[1]);
  const landed = commitsIn(d);
  /*
   * A fixed grid, not a right-flushed row: every session emits the same cells
   * in the same places, so counts and durations line up down the page and a
   * day can be read straight down a column. Empty cells stay empty on purpose.
   */
  return sticky(store.openDays, `session:${s.session_id}`,
    { class: "session", id: `session-${s.session_id}` },
    h("summary", null,
      h("time", { class: "cell-clock" }, `${clock(d.start)}-${clock(d.end)}`),
      h("span", { class: "session-title" }, s.title),
      h("span", { class: "cell-asked" },
        d.prompt_count ? `${d.prompt_count} asked` : ""),
      h("span", { class: "cell-files" },
        files.length
          ? [
              h("span", { class: "muted" }, plural(files.length, "file")),
              h("span", { class: "plus" }, `+${count(d.added)}`),
              h("span", { class: "minus" }, `−${count(d.removed)}`),
            ]
          : null),
      h("span", { class: "cell-commits" },
        landed.length ? plural(landed.length, "commit") : ""),
      h("span", { class: "cell-dur" }, duration(d.active)),
      h("span", { class: "cell-state" },
        s.alive === true ? h("span", { class: "dot live", title: "A claude process is still running here" })
          : s.alive === false ? h("span", { class: "dot quiet", title: "No claude process here any more" })
          : h("span", { class: "dot unknown", title: "Could not tell -- this machine was not reachable" }),
        s.alive === true ? "alive" : s.alive === false ? "ended" : "?"),
      h("code", { class: "session-id" }, s.session_id.slice(0, 8))),
    h("div", { class: "session-body" },
      field("Question by question",
        d.turns.length
          ? h("div", { class: "ledger" },
              d.turns.map((t, i) => turnRow({ turn: t, session: s }, i + 1, s.cwd)))
          : null),
      field("Commits",
        landed.length
          ? h("ul", { class: "plain" }, landed.map((c) =>
              h("li", null,
                h("code", { class: "sha", title: c.sha }, c.short),
                " ",
                c.subject)))
          : null),
      field("Every file touched",
        files.length
          ? h("div", { class: "file-list" },
              files.slice(0, 14).map(([f, n]) =>
                h("div", { class: "file-row" },
                  h("code", { title: f }, relPath(f, s.cwd)),
                  h("span", { class: "muted" }, `x${n}`))))
          : null),
      field("Tools",
        Object.keys(d.tools).length
          ? h("div", { class: "tags" },
              Object.entries(d.tools).sort((a, b) => b[1] - a[1]).map(([t, n]) =>
                h("span", { class: "tag" }, `${t} x${n}`)))
          : null),
      field("Usage",
        h("p", { class: "muted" },
          `in ${count(d.tok_in)} / out ${count(d.tok_out)} / cache ${count(d.cache_read)}`,
          d.cost ? ` / $${d.cost.toFixed(2)}` : "",
          d.errors ? ` / ${d.errors} interrupted` : "",
          Object.keys(s.models).map((m) => ` / ${m}`).join(""))),
      h("div", { class: "row-actions" },
        h("button", { class: "btn ghost", type: "button",
                      data: { copy: resume(s.host, s.cwd, s.session_id) } },
          "Copy resume command"),
        h("button", { class: "btn ghost", type: "button", data: { copy: s.file } },
          "Copy transcript path"),
        s.host === store.localHost
          ? h("button", { class: "btn ghost", type: "button", data: { reveal: s.file } },
              "Show in Finder")
          : null)));
}

export function renderSessions(host: HTMLElement): void {
  const items = slices().filter(keep);
  const badge = document.getElementById("count-sessions");
  if (badge) badge.textContent = items.length ? String(items.length) : "";

  if (!items.length) {
    mount(host, h("div", { class: "empty" },
      h("h3", null, "No sessions in this range"),
      h("p", null, "Widen the range above, or clear the filters.")));
    return;
  }

  const byDay = new Map<string, Map<string, Slice[]>>();
  for (const it of items) {
    const day = byDay.get(it.day) ?? new Map<string, Slice[]>();
    const key = `${it.session.host} ${it.session.cwd ?? "unknown"}`;
    const group = day.get(key) ?? [];
    group.push(it);
    day.set(key, group);
    byDay.set(it.day, day);
  }
  const manyHosts = new Set(items.map((i) => i.session.host)).size > 1;

  mount(host,
    [...byDay.entries()].sort((a, b) => b[0].localeCompare(a[0])).map(([day, groups]) => {
      const all = [...groups.values()].flat();
      return h("section", { class: "day" },
        h("header", { class: "day-head" },
          h("h3", null, dayLabel(day)),
          h("span", { class: "muted" },
            `${plural(all.length, "session")}, ${plural(groups.size, "project")}, `
            + `${duration(all.reduce((a, s) => a + s.data.active, 0))} at the keyboard`)),
        [...groups.entries()]
          .sort((a, b) => basename(a[0].split(" ")[1]).localeCompare(
            basename(b[0].split(" ")[1])))
          .map(([key, list]) => {
            const gap = key.indexOf(" ");
            const hostName = key.slice(0, gap);
            const cwd = key.slice(gap + 1);
            list.sort((a, b) => (a.data.start ?? "").localeCompare(b.data.start ?? ""));
            // What this project's work came to on this day, above the sessions
            // that made it -- the day's answer before the blow-by-blow.
            const ch = changesFor(cwd, day);
            return h("div", { class: "session-group" },
              h("div", { class: "session-group-head" },
                h("strong", null, basename(cwd)),
                manyHosts ? h("span", { class: "tag remote" }, hostName) : null,
                h("code", { class: "project-path", title: cwd }, cwd)),
              ch ? changeSummary(ch) : null,
              list.map(card));
          }));
    }));
}

export function sessionFilterOptions(): { hosts: string[]; projects: string[] } {
  const sessions = store.report?.sessions ?? [];
  return {
    hosts: [...new Set(sessions.map((s) => s.host))].sort(),
    projects: [...new Set(sessions.map((s) => basename(s.cwd)))].sort(),
  };
}
