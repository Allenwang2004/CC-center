/**
 * Every project Claude Code has ever run in, and under each one only the half
 * of the record you wrote: the notes you keep, and the journal for each day.
 *
 * Everything the tool worked out -- what changed, which question did it, what
 * landed in a commit -- lives under Sessions. Splitting it this way keeps the
 * two kinds of truth apart: a project is what you have to say about it.
 */

import { board } from "../ui/board.js";
import { editor } from "../ui/editor/editor.js";
import { h, mount, sticky } from "../ui/dom.js";
import {
  basename, clock, count, dayLabel, duration, firstLine, plural,
  refClock, todayKey,
} from "../core/format.js";
import { changesFor, entriesFor, store } from "../core/store.js";
import type { Commit, Entry, Project, Session, Turn } from "../core/types.js";

interface Row {
  turn: Turn;
  session: Session;
  day: string;
}

interface Group {
  cwd: string;
  host: string;
  branch: string;
  rows: Row[];
  notes: Entry[];
  loose: Commit[];
  meta: Project | null;
  lastActive: number;
  hosts: Set<string>;
}

function collect(): Group[] {
  const groups = new Map<string, Group>();
  const get = (cwd: string): Group => {
    let g = groups.get(cwd);
    if (!g) {
      g = { cwd, host: "", branch: "", rows: [], notes: [], loose: [],
            meta: null, lastActive: 0, hosts: new Set() };
      groups.set(cwd, g);
    }
    return g;
  };

  for (const p of store.report?.projects ?? []) {
    const g = get(p.cwd);
    g.meta = p;
    g.host = p.host;
    g.hosts.add(p.host);
    g.lastActive = Math.max(g.lastActive, p.last_active * 1000);
  }
  for (const s of store.report?.sessions ?? []) {
    const g = get(s.cwd ?? "unknown");
    g.hosts.add(s.host);
    if (!g.host) g.host = s.host;
    if (s.branch && s.branch !== "HEAD") g.branch = s.branch;
    for (const [day, slice] of Object.entries(s.days))
      for (const turn of slice.turns) {
        g.rows.push({ turn, session: s, day });
        g.lastActive = Math.max(g.lastActive, Date.parse(turn.ts ?? "") || 0);
      }
  }
  for (const perHost of Object.values(store.report?.repo_commits ?? {}))
    for (const [cwd, list] of Object.entries(perHost)) get(cwd).loose.push(...list);

  for (const g of groups.values()) {
    g.rows.sort((a, b) => (a.turn.ts ?? "").localeCompare(b.turn.ts ?? ""));
    g.notes = entriesFor(g.cwd, "note");
    for (const n of g.notes) g.lastActive = Math.max(g.lastActive, Date.parse(n.updated_at) || 0);
  }
  return [...groups.values()].sort((a, b) => b.lastActive - a.lastActive);
}

const matches = (turn: Turn, q: string): boolean =>
  !q ||
  [turn.text, ...turn.commands, ...Object.keys(turn.files),
   ...turn.commits.map((c) => `${c.subject} ${c.short}`)]
    .join(" ").toLowerCase().includes(q);

/**
 * A `/plugin` or `/exit` is a row in the ledger but it is not a question, so
 * it must not land in an "asked" count -- the Activity tab counts prompts and
 * the two headers have to agree.
 */
const asked = (rows: Row[]): number => rows.filter((r) => r.turn.kind !== "slash").length;

const didSomething = (t: Turn): boolean =>
  Object.keys(t.files).length > 0 || t.commits.length > 0;

/* -- your half: notes and the journal ------------------------------------ */

/**
 * Notes work the way a notes app works: an index down the left, one note open
 * on the right. Only the open note is mounted, so the pane stays a writing
 * surface rather than a wall of textareas -- and because each editor is held by
 * `keep()`, clicking away and back keeps unsaved text and the caret.
 */
function noteShelf(g: Group): HTMLElement {
  const notes = g.notes;
  const stored = store.openNote.get(g.cwd);
  // Default to the newest note; fall back to the blank form when there are none.
  const current = stored !== undefined ? stored : (notes[0]?.ref ?? "");
  const open = notes.find((n) => n.ref === current) ?? null;
  const composing = !open;

  const select = (ref: string, focus = false): void => {
    store.openNote.set(g.cwd, ref);
    const pane = document.getElementById("pane-projects-body");
    if (pane) renderProjects(pane);
    if (focus) {
      const key = `note:${g.cwd}:${ref || "new"}`;
      (document.querySelector(`[data-editor="${CSS.escape(key)}"]`) as HTMLElement | null)
        ?.querySelector<HTMLElement>("input,textarea")?.focus();
    }
  };

  const ed = editor({
    key: `note:${g.cwd}:${open?.ref ?? "new"}`,
    kind: "note",
    cwd: g.cwd,
    host: open?.host || g.host,
    ref: open?.ref ?? "",
    rows: 12,
    saved: open?.body ?? "",
    markdown: true,
    placeholder: "What you are stuck on, what to pick up next…",
    onCreated: (ref) => select(ref),
    onDeleted: () => {
      // Fall back to whatever is newest once this one is gone.
      store.openNote.delete(g.cwd);
      const pane = document.getElementById("pane-projects-body");
      if (pane) renderProjects(pane);
    },
  });
  if (open) ed.sync(open.body);

  const index = notes.map((n) => {
    const on = n.ref === current;
    const item = h("button",
      { class: `note-item${on ? " is-on" : ""}`, type: "button",
        aria: { current: on ? "true" : "false" } },
      h("time", { class: "note-item-clock" }, refClock(n.ref)),
      h("span", { class: "note-item-title" },
        firstLine(n.body, 60) || "Untitled"),
      h("span", { class: "note-item-date" }, n.ref.slice(5, 10)));
    item.addEventListener("click", () => select(n.ref));
    return item;
  });

  const fresh = h("button",
    { class: `note-item note-item-new${composing ? " is-on" : ""}`, type: "button" },
    h("span", { class: "note-item-title" }, "+ New note"));
  fresh.addEventListener("click", () => select("", true));

  return h("section", { class: "shelf" },
    h("h4", null, "Notes",
      h("span", { class: "muted" }, `${notes.length} kept`)),
    h("div", { class: "note-pane" },
      h("div", { class: "note-index" }, fresh, index),
      h("div", { class: "note-open" }, ed.el)));
}

/**
 * The journal is the other half of the record. Every morning the watcher has
 * Claude write yesterday's entry from the full transcript --- how the day's
 * work was built, one section per mechanism --- and it lands here as saved
 * text. What you add on top is ordinary editing; once you save, the entry is
 * yours and the morning run leaves it alone.
 */
function journalBox(g: Group, day: string): HTMLElement {
  const saved = entriesFor(g.cwd, "journal").find((e) => e.ref === day);
  const ed = editor({
    key: `journal:${g.cwd}:${day}`,
    kind: "journal",
    cwd: g.cwd,
    host: saved?.host || g.host,
    ref: day,
    rows: 10,
    markdown: true,
    images: true,
    saved: saved?.body ?? "",
    placeholder:
      "Written for you the next morning (Settings → Journal), or now with "
      + "`cc-center-app journal --date " + day + "`. Add your own words on top, "
      + "and paste or drop screenshots in.",
  });
  ed.sync(saved?.body ?? "");
  return ed.el;
}

/**
 * One day at a time. The journal used to be a stack of every day in the
 * range, each with its own editor, which made the pane a scroll of mostly
 * empty boxes. Now it works like the notes: a list of days down the left, the
 * one you picked open on the right. The list holds every day you asked
 * something in this range, every day that already has an entry (whatever the
 * range), and today; a dot marks the days with words in them.
 */
function journalShelf(g: Group, byDay: Map<string, Row[]>): HTMLElement {
  const today = todayKey();
  const written = new Map(entriesFor(g.cwd, "journal").map((e) => [e.ref, e]));
  const days = [...new Set([today, ...byDay.keys(), ...written.keys()])]
    .sort((a, b) => b.localeCompare(a));

  const stored = store.openJournal.get(g.cwd);
  const current = stored && days.includes(stored) ? stored : (days[0] ?? today);
  const rows = byDay.get(current) ?? [];
  const saved = written.get(current);
  const ch = changesFor(g.cwd, current);

  const select = (day: string): void => {
    store.openJournal.set(g.cwd, day);
    const pane = document.getElementById("pane-projects-body");
    if (pane) renderProjects(pane);
  };

  const index = days.map((day) => {
    const on = day === current;
    const entry = written.get(day);
    const n = asked(byDay.get(day) ?? []);
    const item = h("button",
      { class: `note-item journal-item${on ? " is-on" : ""}`, type: "button",
        aria: { current: on ? "true" : "false" } },
      h("span", { class: `journal-dot${entry?.body.trim() ? " is-written" : ""}` }),
      h("span", { class: "note-item-title" },
        dayLabel(day),
        day === today && h("span", { class: "tag now" }, "today")),
      h("span", { class: "note-item-date" }, n ? `${n} asked` : ""));
    item.addEventListener("click", () => select(day));
    return item;
  });

  // A journal entry lives in the account, not in the project folder, so the
  // header names when it was last saved rather than a file.
  const head = h("header", { class: "day-head" },
    h("h3", null, dayLabel(current)),
    current === today && h("span", { class: "tag now" }, "today"),
    h("span", { class: "muted" },
      asked(rows) ? `${asked(rows)} asked` : "nothing asked",
      ch && ch.active ? ` · ${duration(ch.active)}` : "",
      saved?.updated_at
        ? ` · saved ${clock(saved.updated_at)} · ${saved.updated_at.slice(0, 10)}`
        : " · not written yet"));

  return h("section", { class: "shelf journal" },
    h("h4", null, "Journal",
      h("span", { class: "muted" }, `${plural(written.size, "day")} written`)),
    h("div", { class: "note-pane journal-pane" },
      h("div", { class: "note-index journal-index" }, index),
      h("div", { class: "note-open journal-open" }, head, journalBox(g, current))));
}

/**
 * The mind map sits above the words: it is the high-level thinking the notes
 * and the journal then spell out. One canvas per project, stored as one row;
 * if two machines each started one, the newest wins and the other stays put.
 */
function boardShelf(g: Group): HTMLElement {
  const saved = entriesFor(g.cwd, "mindmap")
    .sort((a, b) => b.ref.localeCompare(a.ref))[0] ?? null;
  const b = board({
    cwd: g.cwd,
    host: saved?.host || g.host,
    ref: saved?.ref ?? "",
    saved: saved?.body ?? "",
    updatedAt: saved?.updated_at ?? null,
  });
  b.sync(saved?.body ?? "", saved?.ref ?? "", saved?.updated_at ?? null);
  return b.el;
}

/* -- the pane ------------------------------------------------------------ */

/**
 * Rebuilding the pane detaches every node in it, and detaching a focused
 * textarea blurs it -- so a background refresh would throw you out of a note
 * mid-sentence. If you are writing, the redraw waits until you look away.
 */
let deferred: HTMLElement | null = null;

function writingHere(host: HTMLElement): HTMLTextAreaElement | null {
  const active = document.activeElement;
  return active instanceof HTMLTextAreaElement
    && active.classList.contains("writing")
    && host.contains(active)
    ? active
    : null;
}

export function renderProjects(host: HTMLElement): void {
  const writing = writingHere(host);
  if (writing) {
    if (!deferred)
      writing.addEventListener("blur", () => {
        const pending = deferred;
        deferred = null;
        if (pending) renderProjects(pending);
      }, { once: true });
    deferred = host;
    return;
  }
  deferred = null;

  const f = store.projectFilters;
  const q = f.query.trim().toLowerCase();
  let groups = collect();
  if (f.project) groups = groups.filter((g) => basename(g.cwd) === f.project);

  groups = groups.map((g) => ({
    ...g,
    rows: g.rows.filter((r) => matches(r.turn, q) && (!f.changedOnly || didSomething(r.turn))),
  }));
  if (q)
    groups = groups.filter(
      (g) => g.rows.length || g.notes.some((n) => n.body.toLowerCase().includes(q)));
  else if (f.changedOnly) groups = groups.filter((g) => g.rows.length);

  const totalAsked = groups.reduce((a, g) => a + asked(g.rows), 0);
  const totalCommits = groups.reduce(
    (a, g) => a + g.rows.reduce((b, r) => b + r.turn.commits.length, 0), 0);
  const active = groups.filter((g) => g.rows.length).length;

  const meta = document.getElementById("projects-meta");
  if (meta)
    meta.textContent =
      `${plural(groups.length, "project")} · ${active} active in range`
      + ` · ${totalAsked} asked · ${plural(totalCommits, "commit")}`;
  const badge = document.getElementById("count-projects");
  if (badge) badge.textContent = groups.length ? String(groups.length) : "";

  if (!groups.length) {
    mount(host, h("div", { class: "empty" },
      h("h3", null, "No projects yet"),
      h("p", null, "Run Claude Code somewhere and it will show up here.")));
    return;
  }

  mount(host, groups.map((g) => projectBlock(g)));
}

function projectBlock(g: Group): HTMLElement {
  const byDay = new Map<string, Row[]>();
  for (const r of g.rows) {
    const list = byDay.get(r.day) ?? [];
    list.push(r);
    byDay.set(r.day, list);
  }

  const commits = g.rows.reduce((a, r) => a + r.turn.commits.length, 0);
  const added = g.rows.reduce(
    (a, r) => a + Object.values(r.turn.files).reduce((x, s) => x + s.a, 0), 0);
  const removed = g.rows.reduce(
    (a, r) => a + Object.values(r.turn.files).reduce((x, s) => x + s.d, 0), 0);
  const missing = g.meta && !g.meta.exists;


  const loose = g.loose.length
    ? h("section", { class: "day" },
        h("header", { class: "day-head" },
          h("h3", null, "Commits with no question behind them"),
          h("span", { class: "muted" }, "made by hand, or outside the agent")),
        h("div", { class: "ledger" },
          [...g.loose]
            .sort((a, b) => (b.at ?? "").localeCompare(a.at ?? ""))
            .map((c) =>
              h("article", { class: "entry loose" },
                h("time", { class: "margin-clock" }, (c.at ?? "").slice(5, 10)),
                h("div", { class: "entry-body" },
                  h("div", { class: "detail" },
                    h("span", { class: "detail-key" }, ""),
                    h("span", { class: "detail-value" },
                      h("button", { class: "sha", type: "button", title: c.sha,
                                    data: { copy: c.sha } }, c.short),
                      h("span", { class: "commit-subject" }, c.subject),
                      h("span", { class: "muted" }, c.author))))))))
    : null;

  // A project with work in this range opens itself; the rest stay as you left them.
  const block = sticky(store.openProjects, g.cwd,
    { class: "project", open: g.rows.length > 0 },
    h("summary", { class: "project-head" },
      h("h2", null, basename(g.cwd)),
      g.branch && h("span", { class: "tag" }, g.branch),
      [...g.hosts].filter((x) => x && x !== store.localHost)
        .map((x) => h("span", { class: "tag remote" }, x)),
      g.rows.length
        ? h("span", { class: "tag" }, `${asked(g.rows)} asked`)
        : h("span", { class: "tag quiet" }, "quiet in this range"),
      (added || removed) && h("span", { class: "delta" },
        h("span", { class: "plus" }, `+${count(added)}`),
        h("span", { class: "minus" }, `−${count(removed)}`)),
      commits ? h("span", { class: "tag" }, plural(commits, "commit")) : null,
      g.notes.length ? h("span", { class: "tag" }, plural(g.notes.length, "note")) : null,
      missing && h("span", { class: "tag gone" }, "folder is gone"),
      h("code", { class: "project-path", title: g.cwd }, g.cwd)),
    missing ? null : boardShelf(g),
    missing ? null : noteShelf(g),
    journalShelf(g, byDay),
    loose);
  return block;
}

export function projectNames(): string[] {
  return [...new Set(collect().map((g) => basename(g.cwd)))].sort();
}
