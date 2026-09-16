/** One mutable snapshot of the server plus the bits of view state worth keeping. */

import type {
  AppState, Auth, Commit, DaySlice, Entry, PaneName, Report, Session, Settings,
} from "./types.js";

export interface Filters {
  query: string;
  host: string;
  project: string;
  changedOnly: boolean;
}

export interface Store {
  settings: Settings;
  report: Report | null;
  hosts: string[];
  localHost: string;
  serverInfo: { pid: number; cwd: string; started: number };
  pane: PaneName;
  connected: boolean;
  /** Whether `claude` is on the server's PATH, so it can draft a journal. */
  hasClaude: boolean;
  /** Who the server is signed in as; the page only exists while it is. */
  auth: Auth | null;
  filters: Filters;
  projectFilters: { query: string; project: string; changedOnly: boolean };
  openProjects: Set<string>;
  openDays: Set<string>;
  /** Which note each project has open. "" means the blank new-note form. */
  openNote: Map<string, string>;
  /** Which day each project's journal is open on; unset means the newest. */
  openJournal: Map<string, string>;
  /** Projects whose mind map is unfolded (the canvas only mounts when it is). */
  openBoards: Set<string>;
}

export const store: Store = {
  settings: {} as Settings,
  report: null,
  hosts: [],
  localHost: "",
  serverInfo: { pid: 0, cwd: "", started: 0 },
  pane: "agents",
  connected: false,
  hasClaude: false,
  auth: null,
  filters: { query: "", host: "", project: "", changedOnly: false },
  projectFilters: { query: "", project: "", changedOnly: true },
  openProjects: new Set(),
  openDays: new Set(),
  openNote: new Map(),
  openJournal: new Map(),
  openBoards: new Set(),
};

export function adopt(state: AppState): void {
  store.settings = state.settings;
  store.hosts = state.hosts;
  store.localHost = state.local_host;
  store.serverInfo = { pid: state.pid, cwd: state.cwd, started: state.started };
  store.hasClaude = state.claude;
  store.auth = state.auth;
  store.report = state.report;
}

/** Every (session, day) pair in the window, flattened for listing. */
export interface Slice {
  session: Session;
  day: string;
  data: Session["days"][string];
}

export function slices(): Slice[] {
  const out: Slice[] = [];
  for (const session of store.report?.sessions ?? [])
    for (const [day, data] of Object.entries(session.days)) out.push({ session, day, data });
  return out.sort((a, b) => (b.data.start ?? "").localeCompare(a.data.start ?? ""));
}

export const entriesFor = (cwd: string, kind: Entry["kind"]): Entry[] =>
  (store.report?.entries[cwd] ?? []).filter((e) => e.kind === kind);

export const changesFor = (cwd: string, day: string) =>
  store.report?.changes[cwd]?.[day] ?? null;

/**
 * The commits a day's work actually landed.
 *
 * `DaySlice.commits` is not this: it holds the messages scraped out of
 * `git commit -m` command lines, so it counts commits that were only ever
 * talked about (a message quoted inside a heredoc, a commit in a directory
 * that is not even a repo) and misses ones made any other way. Turn commits
 * come back from `git log`, so they are the ones that exist.
 */
export const commitsIn = (d: DaySlice): Commit[] =>
  d.turns.flatMap((t) => t.commits);

/** Sessions needing you, most urgent first. */
export const waiting = (): Session[] =>
  (store.report?.sessions ?? []).filter((s) => s.attention);
