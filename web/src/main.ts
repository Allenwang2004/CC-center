/**
 * Wiring: bind once, then load, then let the server push.
 *
 * The page has two states. Until the server is signed in to Supabase it is the
 * sign-in screen and nothing else is fetched; once it is, `start()` loads the
 * state and opens the event stream. A 401 from any call -- the session was
 * revoked, or signed out from another tab -- drops back to the sign-in screen.
 */

import { SIGNED_OUT, api, listen } from "./core/api.js";
import { $, $$, composing, h } from "./ui/dom.js";
import { unsavedCount } from "./ui/editor/editor.js";
import { adopt, store } from "./core/store.js";
import {
  bindMachineDrag, fillRangeFields, renderMachines, renderRange,
  renderRemoteToggle, renderScope, renderStatus,
} from "./ui/sidebar.js";
import { renderAgents } from "./views/agents.js";
import { renderActivity, renderReport } from "./views/activity.js";
import { projectNames, renderProjects } from "./views/projects.js";
import { renderSessions, sessionFilterOptions } from "./views/sessions.js";
import { bindSettings, renderSettings } from "./views/settings.js";
import { bindSignin, hideSignin, showSignin } from "./views/signin.js";
import type { AppState, Auth, LogLine, PaneName, Settings } from "./core/types.js";

const PANES: PaneName[] = ["agents", "projects", "sessions", "activity", "report", "settings"];

/* -- small helpers ------------------------------------------------------- */

let toastTimer: number | undefined;

function toast(message: string): void {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = message;
  el.classList.add("show");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => el.classList.remove("show"), 2600);
}

async function copy(text: string, note: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    toast(note);
  } catch {
    toast("Could not reach the clipboard");
  }
}

async function save(patch: Partial<Settings>): Promise<void> {
  const res = await api.settings(patch);
  store.settings = res.settings;
  paintChrome();
}

/* -- rendering ----------------------------------------------------------- */

function paintChrome(): void {
  renderStatus(store.connected);
  renderScope();
  renderRange(save);
  renderMachines(save, (hosts) => void api.refresh(hosts));
  renderRemoteToggle(save, () => void api.refresh());
  fillRangeFields();
  renderSettings();
}

function fillSelect(id: string, values: string[], current: string, all: string): void {
  const el = document.getElementById(id) as HTMLSelectElement | null;
  if (!el) return;
  el.replaceChildren(
    h("option", { value: "" }, all),
    ...values.map((v) => h("option", { value: v }, v)),
  );
  el.value = current;
}

const paneBody = (name: PaneName): HTMLElement | null =>
  document.getElementById(`pane-${name}-body`);

function renderPane(): void {
  const host = paneBody(store.pane);
  if (!host) return;
  if (store.pane === "agents") renderAgents(host);
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
  if (store.pane === "activity") renderActivity(host);
  if (store.pane === "report") void loadReport();
  if (store.pane === "settings") renderSettings();
}

function renderAll(): void {
  paintChrome();
  const agents = paneBody("agents");
  if (agents) renderAgents(agents);
  if (store.pane !== "agents") renderPane();
}

async function loadReport(): Promise<void> {
  const host = paneBody("report");
  if (!host) return;
  try {
    store.reportText = await api.markdown(
      store.reportView, store.settings.prompts, store.settings.tokens);
  } catch (err) {
    store.reportText = err instanceof Error ? err.message : "Could not build the report";
  }
  renderReport(host);
}

function showPane(name: PaneName): void {
  store.pane = name;
  for (const p of PANES) {
    const tab = $(`#tabs button[data-pane="${p}"]`);
    tab?.setAttribute("aria-selected", String(p === name));
    const pane = document.getElementById(`pane-${p}`);
    if (pane) pane.hidden = p !== name;
  }
  renderPane();
}

/* -- events -------------------------------------------------------------- */

function bind(): void {
  document.addEventListener("click", (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLElement>("button[data-pane]");
    if (btn?.dataset.pane) showPane(btn.dataset.pane as PaneName);
  });

  document.addEventListener("click", (e) => {
    const el = (e.target as HTMLElement).closest<HTMLElement>("[data-copy],[data-reveal],[data-focus-session],[data-remove-host]");
    if (!el) return;
    if (el.dataset.copy !== undefined) void copy(el.dataset.copy, "Copied");
    if (el.dataset.reveal) void api.reveal(el.dataset.reveal).catch((x) => toast(String(x)));
    if (el.dataset.focusSession) {
      showPane("sessions");
      const target = document.getElementById(`session-${el.dataset.focusSession}`);
      if (target instanceof HTMLDetailsElement) {
        target.open = true;
        target.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    }
    if (el.dataset.removeHost) {
      const next = store.hosts.filter((x) => x !== el.dataset.removeHost);
      store.hosts = next;
      void api.hosts(next).then(paintChrome);
    }
  });

  const debounce = (fn: () => void, ms = 120) => {
    let t: number | undefined;
    return () => {
      window.clearTimeout(t);
      t = window.setTimeout(fn, ms);
    };
  };

  const bindInput = (id: string, apply: (value: string) => void) =>
    document.getElementById(id)?.addEventListener("input", debounce(function (this: void) {
      const el = document.getElementById(id) as HTMLInputElement;
      apply(el.value);
      renderPane();
    }));

  bindInput("search-projects", (v) => (store.projectFilters.query = v));
  bindInput("search-sessions", (v) => (store.filters.query = v));

  document.getElementById("filter-project")?.addEventListener("change", (e) => {
    store.projectFilters.project = (e.target as HTMLSelectElement).value;
    renderPane();
  });
  document.getElementById("changed-projects")?.addEventListener("change", (e) => {
    store.projectFilters.changedOnly = (e.target as HTMLInputElement).checked;
    renderPane();
  });
  document.getElementById("filter-host")?.addEventListener("change", (e) => {
    store.filters.host = (e.target as HTMLSelectElement).value;
    renderPane();
  });
  document.getElementById("filter-session-project")?.addEventListener("change", (e) => {
    store.filters.project = (e.target as HTMLSelectElement).value;
    renderPane();
  });
  document.getElementById("changed-sessions")?.addEventListener("change", (e) => {
    store.filters.changedOnly = (e.target as HTMLInputElement).checked;
    renderPane();
  });

  document.getElementById("report-view")?.addEventListener("change", (e) => {
    store.reportView = (e.target as HTMLSelectElement).value as "day" | "project";
    void loadReport();
  });
  document.getElementById("report-raw")?.addEventListener("click", () => {
    store.showRaw = !store.showRaw;
    const body = paneBody("report");
    if (body) renderReport(body);
  });
  document.getElementById("report-copy")?.addEventListener("click", () =>
    void copy(store.reportText, "Report copied"));
  document.getElementById("report-save")?.addEventListener("click", async () => {
    try {
      const res = await api.exportFile("markdown");
      toast(`Saved to ${res.path}`);
    } catch (err) {
      toast(err instanceof Error ? err.message : "Could not save");
    }
  });

  document.getElementById("refresh")?.addEventListener("click", () => {
    void api.refresh();
    toast("Collecting");
  });
  document.getElementById("date")?.addEventListener("change", (e) =>
    void save({ date: (e.target as HTMLInputElement).value }));

  document.getElementById("tz")?.addEventListener("change", (e) =>
    void save({ tz: (e.target as HTMLInputElement).value.trim() }));
  bindSettings(save);

  document.getElementById("add-host")?.addEventListener("keydown", (e) => {
    const input = e.target as HTMLInputElement;
    if (composing(e as KeyboardEvent)) return;
    if ((e as KeyboardEvent).key !== "Enter" || !input.value.trim()) return;
    const next = [...store.hosts, input.value.trim()];
    input.value = "";
    store.hosts = next;
    void api.hosts(next).then(paintChrome);
  });

  document.addEventListener("keydown", (e) => {
    if (composing(e) || e.metaKey || e.ctrlKey || e.altKey) return;
    const target = e.target as HTMLElement;
    if (target.matches("input, textarea, select")) {
      if (e.key === "Escape") target.blur();
      return;
    }
    if (e.key === "r" || e.key === "R") {
      void api.refresh();
      toast("Collecting");
      return;
    }
    if (e.key === "/") {
      e.preventDefault();
      showPane("projects");
      (document.getElementById("search-projects") as HTMLInputElement | null)?.focus();
      return;
    }
    const index = Number(e.key);
    if (index >= 1 && index <= PANES.length) showPane(PANES[index - 1]!);
  });

  bindSidebarResize();
  bindMachineDrag();
}

function bindSidebarResize(): void {
  const handle = document.getElementById("resizer");
  const shell = document.querySelector<HTMLElement>(".shell");
  if (!handle || !shell) return;
  const apply = (w: number) => shell.style.setProperty("--sidebar", `${w}px`);
  apply(store.settings.sidebar_w || 340);

  let dragging = false;
  handle.addEventListener("pointerdown", (e) => {
    dragging = true;
    handle.setPointerCapture(e.pointerId);
  });
  handle.addEventListener("pointermove", (e) => {
    if (dragging) apply(Math.min(520, Math.max(220, e.clientX)));
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

function logLine(line: LogLine): void {
  const host = document.getElementById("log-lines");
  const drawer = document.getElementById("log");
  if (!host || !drawer) return;

  const text = (line.host ? `[${line.host}] ` : "") + line.msg;
  const last = host.lastElementChild as HTMLElement | null;

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
  while (host.children.length > 120) host.firstChild?.remove();
  host.scrollTop = host.scrollHeight;
  if (line.level === "error" || line.level === "warn") drawer.hidden = false;
}

async function reload(): Promise<void> {
  store.report = await api.report();
  if (store.report.host_list) store.hosts = store.report.host_list;
  renderAll();
}

/* -- boot ---------------------------------------------------------------- */

let stream: EventSource | null = null;

/** Drop to the sign-in screen: close the stream, forget nothing else. */
function lock(auth: Auth | null): void {
  stream?.close();
  stream = null;
  store.connected = false;
  showSignin(auth);
}

/** Load the state and open the stream. Only ever runs signed in. */
async function start(): Promise<void> {
  const state = await api.state();
  if (!state.auth.signed_in) {
    lock(state.auth);
    return;
  }
  hideSignin();
  adopt(state as AppState);
  (state as AppState).log?.forEach(logLine);
  const drawer = document.getElementById("log");
  if (drawer) drawer.hidden = true;

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
    update: () => void reload().catch(() => {}),
    hosts: () => void reload().catch(() => {}),
    attention: () => void reload().catch(() => {}),
    log: (line) => logLine(line as LogLine),
    settings: (s) => {
      store.settings = s;
      paintChrome();
    },
  });
}

async function boot(): Promise<void> {
  bind();
  bindSignin(() => void start().catch((err) => toast(String(err))));
  window.addEventListener(SIGNED_OUT, (e) => lock((e as CustomEvent<Auth | null>).detail));

  // Saving is explicit now, so closing the tab is the one way to lose writing.
  window.addEventListener("beforeunload", (e) => {
    if (!unsavedCount()) return;
    e.preventDefault();
    e.returnValue = "";
  });

  document.getElementById("log-close")?.addEventListener("click", () => {
    const el = document.getElementById("log");
    if (el) el.hidden = true;
  });

  document.getElementById("sign-out")?.addEventListener("click", async () => {
    if (unsavedCount() && !confirm("Unsaved writing will be lost. Sign out anyway?")) return;
    // Close the stream first: the server announces the sign-out to every tab,
    // and this one would otherwise reload straight into a 401.
    stream?.close();
    stream = null;
    try {
      const res = await api.auth.signout();
      lock(res.auth);
    } catch (err) {
      toast(err instanceof Error ? err.message : "Could not sign out");
    }
  });
  document.getElementById("sync-now")?.addEventListener("click", () => {
    void api.sync().then(() => toast("Syncing")).catch((err) => toast(String(err)));
  });

  window.setInterval(() => {
    if (!stream) return;
    renderStatus(store.connected);
    const agents = paneBody("agents");
    if (store.pane === "agents" && agents) renderAgents(agents);
  }, 15000);

  void $$;
  await start();
}

void boot();
