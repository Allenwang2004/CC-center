/** One mutable snapshot of the server plus the bits of view state worth keeping. */
export const store = {
    settings: {},
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
};
export function adopt(state) {
    store.settings = state.settings;
    store.hosts = state.hosts;
    store.localHost = state.local_host;
    store.serverInfo = { pid: state.pid, cwd: state.cwd, started: state.started };
    store.hasClaude = state.claude;
    store.auth = state.auth;
    store.report = state.report;
}
export function slices() {
    const out = [];
    for (const session of store.report?.sessions ?? [])
        for (const [day, data] of Object.entries(session.days))
            out.push({ session, day, data });
    return out.sort((a, b) => (b.data.start ?? "").localeCompare(a.data.start ?? ""));
}
export const entriesFor = (cwd, kind) => (store.report?.entries[cwd] ?? []).filter((e) => e.kind === kind);
export const changesFor = (cwd, day) => store.report?.changes[cwd]?.[day] ?? null;
/**
 * The commits a day's work actually landed.
 *
 * `DaySlice.commits` is not this: it holds the messages scraped out of
 * `git commit -m` command lines, so it counts commits that were only ever
 * talked about (a message quoted inside a heredoc, a commit in a directory
 * that is not even a repo) and misses ones made any other way. Turn commits
 * come back from `git log`, so they are the ones that exist.
 */
export const commitsIn = (d) => d.turns.flatMap((t) => t.commits);
/** Sessions needing you, most urgent first. */
export const waiting = () => (store.report?.sessions ?? []).filter((s) => s.attention);
//# sourceMappingURL=store.js.map