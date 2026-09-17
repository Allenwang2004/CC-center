/**
 * The knobs you set once and leave: how often to collect, when to be told,
 * what the report prints, and the server itself. They used to live down the
 * sidebar; the sidebar now keeps only the machines, and everything else is
 * here (the range, which changes during a day, sits on the tabs it shapes).
 *
 * The fields are static HTML in index.html. This module only fills them from
 * the store and writes a change back, so a control can be moved on the page
 * without touching code -- it is found by id, not by position.
 */
import { api, inApp } from "../core/api.js";
import { ago, plural } from "../core/format.js";
import { store } from "../core/store.js";
const NUMBERS = ["local_poll", "remote_poll", "remote_poll_hot", "live_window",
    "notify_waiting_after", "notify_tool_after"];
const TEXTS = ["browser", "journal_at", "journal_model", "tz"];
const FLAGS = ["sidechains", "oneshot", "notify_sound", "journal_auto"];
export function renderSettings() {
    const s = store.settings;
    const set = (id, value) => {
        const el = document.getElementById(id);
        // Never overwrite a field the reader is in the middle of editing.
        if (!el || el === document.activeElement)
            return;
        if (el instanceof HTMLInputElement && el.type === "checkbox")
            el.checked = Boolean(value);
        else
            el.value = String(value);
    };
    for (const id of NUMBERS)
        set(id, s[id]);
    for (const id of TEXTS)
        set(id, s[id]);
    for (const id of FLAGS)
        set(id, s[id]);
    set("notify_scope", s.notify_scope);
    // Inside the app the page has no port of its own; the server is a sidecar.
    const where = inApp ? "app" : `port ${location.port}`;
    const server = `pid ${store.serverInfo.pid} / ${where} / ${store.serverInfo.cwd}`;
    const info = document.getElementById("server-info");
    if (info)
        info.textContent = server;
    const foot = document.getElementById("sidebar-server");
    if (foot)
        foot.textContent = where;
    const quit = document.getElementById("quit");
    if (quit)
        quit.textContent = inApp ? "Quit cc-center" : "Stop the server";
    const dbInfo = document.getElementById("db-info");
    if (dbInfo && store.report)
        dbInfo.textContent =
            `${plural(store.report.store.journals, "journal entry", "journal entries")}`
                + `, ${plural(store.report.store.notes, "note")} cached locally`;
    const noClaude = document.getElementById("journal-no-claude");
    if (noClaude)
        noClaude.hidden = store.hasClaude;
    renderAccount();
}
/**
 * Who the server is signed in as, and how fresh the local copy is. `auth`
 * rides along with every report, so a sync finishing elsewhere updates this
 * line without a reload.
 */
function renderAccount() {
    const auth = store.report?.auth ?? store.auth;
    const email = document.getElementById("account-email");
    if (email)
        email.textContent = auth?.email ?? "";
    const sync = document.getElementById("account-sync");
    if (sync) {
        const at = auth?.synced_at;
        const when = at ? `Last synced ${ago(Date.now() / 1000 - at)}` : "Not synced yet";
        const pending = auth?.pending
            ? `; ${plural(auth.pending, "entry", "entries")} still only on this machine` : "";
        const project = auth?.project ? ` (${auth.project})` : "";
        sync.textContent = `${when}${pending}${project}`;
    }
    const error = document.getElementById("account-error");
    if (error) {
        error.textContent = auth?.error ?? "";
        error.hidden = !auth?.error;
    }
}
export function bindSettings(save) {
    for (const id of NUMBERS)
        document.getElementById(id)?.addEventListener("change", (e) => void save({ [id]: Number(e.target.value) }));
    for (const id of TEXTS)
        document.getElementById(id)?.addEventListener("change", (e) => void save({ [id]: e.target.value.trim() }));
    for (const id of FLAGS)
        document.getElementById(id)?.addEventListener("change", (e) => void save({ [id]: e.target.checked }));
    document.getElementById("notify_scope")?.addEventListener("change", (e) => void save({ notify_scope: e.target.value }));
    document.getElementById("quit")?.addEventListener("click", () => {
        if (confirm(inApp ? "Quit cc-center? Watching stops until you open it again." : "Stop the server?"))
            void api.quit();
    });
}
//# sourceMappingURL=settings.js.map