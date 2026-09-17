/**
 * Wiring: bind once, then load, then let the server push.
 *
 * The page has two states. Until the server is signed in to Supabase it is the
 * sign-in screen and nothing else is fetched; once it is, `start()` loads the
 * state and opens the event stream. A 401 from any call -- the session was
 * revoked, or signed out from another tab -- drops back to the sign-in screen.
 */
import { SIGNED_OUT, api, inApp, listen } from "./core/api.js";
import { $, $$, composing, h, toast } from "./ui/dom.js";
import { unsavedCount } from "./ui/editor/editor.js";
import { adopt, store } from "./core/store.js";
import { bindMachineDrag, renderMachines, renderRange, renderRemoteToggle, renderScope, renderStatus, } from "./ui/sidebar.js";
import { renderAgents } from "./views/agents.js";
import { renderActivity } from "./views/activity.js";
import { loadClaudeFiles, renderClaude, setClaudeQuery } from "./views/claudemd.js";
import { projectNames, renderProjects } from "./views/projects.js";
import { renderSessions, sessionFilterOptions } from "./views/sessions.js";
import { bindSettings, renderSettings } from "./views/settings.js";
import { bindSignin, hideSignin, showSignin } from "./views/signin.js";
const PANES = ["agents", "projects", "sessions", "activity", "claude", "settings"];
/* -- small helpers ------------------------------------------------------- */
async function copy(text, note) {
    try {
        await navigator.clipboard.writeText(text);
        toast(note);
    }
    catch {
        toast("Could not reach the clipboard");
    }
}
async function save(patch) {
    const res = await api.settings(patch);
    store.settings = res.settings;
    paintChrome();
}
/* -- rendering ----------------------------------------------------------- */
function paintChrome() {
    renderStatus(store.connected);
    renderScope();
    renderRange(save);
    renderMachines(save, (hosts) => void api.refresh(hosts));
    renderRemoteToggle(save, () => void api.refresh());
    renderSettings();
}
function fillSelect(id, values, current, all) {
    const el = document.getElementById(id);
    if (!el)
        return;
    el.replaceChildren(h("option", { value: "" }, all), ...values.map((v) => h("option", { value: v }, v)));
    el.value = current;
}
const paneBody = (name) => document.getElementById(`pane-${name}-body`);
function renderPane() {
    const host = paneBody(store.pane);
    if (!host)
        return;
    if (store.pane === "agents")
        renderAgents(host);
    if (store.pane === "projects") {
        fillSelect("filter-project", projectNames(), store.projectFilters.project, "All projects");
        renderProjects(host);
    }
    if (store.pane === "sessions") {
        const options = sessionFilterOptions();
        fillSelect("filter-host", options.hosts, store.filters.host, "All machines");
        fillSelect("filter-session-project", options.projects, store.filters.project, "All projects");
        renderSessions(host);
    }
    if (store.pane === "activity")
        renderActivity(host);
    if (store.pane === "claude")
        renderClaude(host);
    if (store.pane === "settings")
        renderSettings();
}
function renderAll() {
    paintChrome();
    const agents = paneBody("agents");
    if (agents)
        renderAgents(agents);
    if (store.pane !== "agents")
        renderPane();
}
function showPane(name) {
    const was = store.pane;
    store.pane = name;
    for (const p of PANES) {
        const tab = $(`#tabs button[data-pane="${p}"]`);
        tab?.setAttribute("aria-selected", String(p === name));
        const pane = document.getElementById(`pane-${p}`);
        if (pane)
            pane.hidden = p !== name;
    }
    // The window's label only means something where the window applies.
    const scope = document.getElementById("scope");
    if (scope)
        scope.hidden = name !== "sessions" && name !== "activity";
    renderPane();
    // CLAUDE.md files are read from disk, not from the report: fetch them on the way in.
    const claude = paneBody("claude");
    if (name === "claude" && was !== "claude" && claude)
        void loadClaudeFiles(claude);
}
/* -- events -------------------------------------------------------------- */
function bind() {
    document.addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-pane]");
        if (btn?.dataset.pane)
            showPane(btn.dataset.pane);
    });
    document.addEventListener("click", (e) => {
        const el = e.target.closest("[data-copy],[data-reveal],[data-focus-session],[data-remove-host]");
        if (!el)
            return;
        if (el.dataset.copy !== undefined)
            void copy(el.dataset.copy, "Copied");
        if (el.dataset.reveal)
            void api.reveal(el.dataset.reveal).catch((x) => toast(String(x)));
        if (el.dataset.focusSession) {
            showPane("sessions");
            const target = document.getElementById(`session-${el.dataset.focusSession}`);
            if (target instanceof HTMLDetailsElement) {
                target.open = true;
                target.scrollIntoView({ block: "center", behavior: "smooth" });
            }
        }
        if (el.dataset.removeHost) {
            void api.removeHost(el.dataset.removeHost)
                .then((res) => {
                store.hosts = res.hosts;
                paintChrome();
            })
                .catch((err) => toast(err instanceof Error ? err.message : "Could not remove the host"));
        }
    });
    const debounce = (fn, ms = 120) => {
        let t;
        return () => {
            window.clearTimeout(t);
            t = window.setTimeout(fn, ms);
        };
    };
    const bindInput = (id, apply) => document.getElementById(id)?.addEventListener("input", debounce(function () {
        const el = document.getElementById(id);
        apply(el.value);
        renderPane();
    }));
    bindInput("search-projects", (v) => (store.projectFilters.query = v));
    bindInput("search-sessions", (v) => (store.filters.query = v));
    document.getElementById("filter-project")?.addEventListener("change", (e) => {
        store.projectFilters.project = e.target.value;
        renderPane();
    });
    document.getElementById("filter-host")?.addEventListener("change", (e) => {
        store.filters.host = e.target.value;
        renderPane();
    });
    document.getElementById("filter-session-project")?.addEventListener("change", (e) => {
        store.filters.project = e.target.value;
        renderPane();
    });
    document.getElementById("changed-sessions")?.addEventListener("change", (e) => {
        store.filters.changedOnly = e.target.checked;
        renderPane();
    });
    bindInput("search-claude", (v) => setClaudeQuery(v));
    document.getElementById("claude-reload")?.addEventListener("click", () => {
        const body = paneBody("claude");
        if (body)
            void loadClaudeFiles(body);
    });
    document.getElementById("refresh")?.addEventListener("click", () => {
        void api.refresh();
        toast("Collecting");
    });
    for (const picker of document.querySelectorAll(".range-date"))
        picker.addEventListener("change", () => void save({ date: picker.value }));
    bindSettings(save);
    document.getElementById("add-host")?.addEventListener("keydown", (e) => {
        const input = e.target;
        if (composing(e))
            return;
        if (e.key !== "Enter" || !input.value.trim())
            return;
        const host = input.value.trim();
        if (store.hosts.includes(host) || host === store.localHost) {
            toast(`${host} is already in the list`);
            return;
        }
        input.value = "";
        void api.addHost(host)
            .then((res) => {
            store.hosts = res.hosts;
            paintChrome();
            // Collect from it right away instead of waiting for the next remote pass.
            void api.refresh([host]);
            toast(`Added ${host}, collecting`);
        })
            .catch((err) => {
            input.value = host;
            toast(err instanceof Error ? err.message : "Could not add the host");
        });
    });
    document.addEventListener("keydown", (e) => {
        if (composing(e) || e.metaKey || e.ctrlKey || e.altKey)
            return;
        const target = e.target;
        if (target.matches("input, textarea, select")) {
            if (e.key === "Escape")
                target.blur();
            return;
        }
        // The mind map has its own keys (digits pick tools, R is rectangle...).
        if (target.closest(".board"))
            return;
        if (e.key === "r" || e.key === "R") {
            void api.refresh();
            toast("Collecting");
            return;
        }
        if (e.key === "/") {
            e.preventDefault();
            showPane("projects");
            document.getElementById("search-projects")?.focus();
            return;
        }
        const index = Number(e.key);
        if (index >= 1 && index <= PANES.length)
            showPane(PANES[index - 1]);
    });
    bindSidebarResize();
    bindMachineDrag();
}
function bindSidebarResize() {
    const handle = document.getElementById("resizer");
    const shell = document.querySelector(".shell");
    if (!handle || !shell)
        return;
    const apply = (w) => shell.style.setProperty("--sidebar", `${w}px`);
    apply(store.settings.sidebar_w || 340);
    let dragging = false;
    handle.addEventListener("pointerdown", (e) => {
        dragging = true;
        handle.setPointerCapture(e.pointerId);
    });
    handle.addEventListener("pointermove", (e) => {
        if (dragging)
            apply(Math.min(520, Math.max(220, e.clientX)));
    });
    handle.addEventListener("pointerup", (e) => {
        dragging = false;
        handle.releasePointerCapture(e.pointerId);
        void save({ sidebar_w: Math.round(parseFloat(getComputedStyle(shell).getPropertyValue("--sidebar"))) });
    });
    handle.addEventListener("dblclick", () => {
        apply(300);
        void save({ sidebar_w: 340 });
    });
}
function logLine(line) {
    const host = document.getElementById("log-lines");
    const drawer = document.getElementById("log");
    if (!host || !drawer)
        return;
    const text = (line.host ? `[${line.host}] ` : "") + line.msg;
    const last = host.lastElementChild;
    /*
     * A standing condition -- remote collection switched off, a host that is
     * still unreachable -- says the same sentence on every pass. Printing it
     * again buries everything else and re-opens a drawer the reader just closed,
     * so a verbatim repeat only bumps a counter on the line already there.
     */
    if (last && last.dataset.line === text) {
        const n = Number(last.dataset.n ?? "1") + 1;
        last.dataset.n = String(n);
        last.textContent = `${text}  x${n}`;
        host.scrollTop = host.scrollHeight;
        return;
    }
    host.appendChild(h("div", { class: line.level || "info", data: { line: text } }, text));
    while (host.children.length > 120)
        host.firstChild?.remove();
    host.scrollTop = host.scrollHeight;
    if (line.level === "error" || line.level === "warn")
        drawer.hidden = false;
}
async function reload() {
    store.report = await api.report();
    if (store.report.host_list)
        store.hosts = store.report.host_list;
    renderAll();
}
/* -- boot ---------------------------------------------------------------- */
let stream = null;
/** Drop to the sign-in screen: close the stream, forget nothing else. */
function lock(auth) {
    stream?.close();
    stream = null;
    store.connected = false;
    showSignin(auth);
}
/** Load the state and open the stream. Only ever runs signed in. */
async function start() {
    const booting = document.getElementById("booting");
    // In the app the first call waits for the sidecar; say so instead of a blank window.
    if (booting && inApp && !store.report)
        booting.hidden = false;
    let state;
    try {
        state = await api.state();
    }
    finally {
        if (booting)
            booting.hidden = true;
    }
    if (!state.auth.signed_in) {
        lock(state.auth);
        return;
    }
    hideSignin();
    adopt(state);
    state.log?.forEach(logLine);
    const drawer = document.getElementById("log");
    if (drawer)
        drawer.hidden = true;
    showPane(store.pane || "agents");
    renderAll();
    stream?.close();
    stream = listen({
        open: () => {
            store.connected = true;
            renderStatus(true);
        },
        closed: () => {
            store.connected = false;
            renderStatus(false);
        },
        update: () => void reload().catch(() => { }),
        hosts: () => void reload().catch(() => { }),
        attention: () => void reload().catch(() => { }),
        log: (line) => logLine(line),
        settings: (s) => {
            store.settings = s;
            paintChrome();
        },
        // From the menu bar or a notification: open on that session.
        focus: (sessionId) => {
            showPane("agents");
            window.setTimeout(() => {
                const card = document.getElementById(`agent-${sessionId}`);
                card?.scrollIntoView({ block: "center", behavior: "smooth" });
                card?.classList.add("is-focused");
                window.setTimeout(() => card?.classList.remove("is-focused"), 2400);
            }, 50);
        },
    });
}
async function boot() {
    bind();
    bindSignin(() => void start().catch((err) => toast(String(err))));
    window.addEventListener(SIGNED_OUT, (e) => lock(e.detail));
    // Saving is explicit now, so closing the tab is the one way to lose writing.
    window.addEventListener("beforeunload", (e) => {
        if (!unsavedCount())
            return;
        e.preventDefault();
        e.returnValue = "";
    });
    document.getElementById("log-close")?.addEventListener("click", () => {
        const el = document.getElementById("log");
        if (el)
            el.hidden = true;
    });
    document.getElementById("sign-out")?.addEventListener("click", async () => {
        if (unsavedCount() && !confirm("Unsaved writing will be lost. Sign out anyway?"))
            return;
        // Close the stream first: the server announces the sign-out to every tab,
        // and this one would otherwise reload straight into a 401.
        stream?.close();
        stream = null;
        try {
            const res = await api.auth.signout();
            lock(res.auth);
        }
        catch (err) {
            toast(err instanceof Error ? err.message : "Could not sign out");
        }
    });
    document.getElementById("sync-now")?.addEventListener("click", () => {
        void api.sync().then(() => toast("Syncing")).catch((err) => toast(String(err)));
    });
    window.setInterval(() => {
        if (!stream)
            return;
        renderStatus(store.connected);
        const agents = paneBody("agents");
        if (store.pane === "agents" && agents)
            renderAgents(agents);
    }, 15000);
    void $$;
    await start();
}
void boot();
//# sourceMappingURL=main.js.map