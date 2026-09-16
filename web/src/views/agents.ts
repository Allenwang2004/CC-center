/**
 * The first question this tool answers: is anything waiting on me?
 *
 * This is the one place saturation is allowed to be loud. Everything else in
 * the interface stays achromatic so that an amber card here cannot be missed.
 */

import { api } from "../core/api.js";
import { h, mount, toast } from "../ui/dom.js";
import { ago, basename, clock, count, duration, epoch, whenLabel } from "../core/format.js";
import { store } from "../core/store.js";
import type { LimitWindow, Session } from "../core/types.js";

const serverNow = (): number => (store.report ? store.report.server_now : Date.now() / 1000);
const secondsSince = (iso: string | null | undefined): number => serverNow() - epoch(iso);

/* -- meters ------------------------------------------------------------- */

/** Green while there is room, amber from 60%, red from 85%. The number says the same. */
const grade = (pct: number): string => (pct >= 85 ? "high" : pct >= 60 ? "mid" : "");

function meter(pct: number | null, cls = ""): HTMLElement {
  const p = pct === null ? 0 : Math.max(0, Math.min(100, pct));
  return h("span", { class: `meter ${cls} ${grade(p)}`, role: "meter",
                     aria: { valuenow: String(Math.round(p)), valuemin: "0", valuemax: "100" } },
    h("span", { class: "meter-fill", style: { width: `${p}%` } }));
}

/** "ctx 12%" with a sliver of bar -- how much of this session's window is spoken for. */
function ctxMeter(s: Session): HTMLElement | null {
  const c = s.ctx;
  if (!c || !c.size) return null;
  const pct = (c.used / c.size) * 100;
  return h("span", { class: "ctx",
                     title: `${count(c.used)} of ${count(c.size)} tokens in context`
                       + (c.model ? ` · ${c.model}` : "") },
    h("span", { class: "muted" }, "ctx"),
    meter(pct),
    h("span", { class: "ctx-pct" }, `${Math.round(pct)}%`));
}

function resetLabel(w: LimitWindow): string {
  if (!w.resets_at) return "";
  const left = w.resets_at - serverNow();
  if (left <= 0) return `reset ${ago(-left)}`;
  return left < 86400 ? `resets in ${duration(left)}` : `resets ${whenLabel(w.resets_at)}`;
}

function planRow(name: string, w: LimitWindow | undefined): HTMLElement {
  if (!w) return h("div", { class: "plan-row muted" },
    h("span", { class: "plan-name" }, name), h("span", null, "not reported"));
  const expired = Boolean(w.resets_at && w.resets_at <= serverNow());
  const cls = expired ? "stale" : grade(w.used_percentage);
  return h("div", { class: `plan-row ${cls}` },
    h("span", { class: "plan-name" }, name),
    meter(expired ? null : w.used_percentage),
    h("span", { class: "plan-pct" }, expired ? "—" : `${Math.round(w.used_percentage)}%`),
    h("span", { class: "muted" }, resetLabel(w)));
}

/**
 * The plan's rolling limits. These never appear in a transcript; Claude Code
 * only ever tells its status line, so the numbers are whatever the last
 * status-line call on any machine handed to `cc-center-app statusline`.
 */
function planBlock(): HTMLElement {
  const u = store.report?.usage ?? null;
  if (!u) {
    const hook = `${store.serverInfo.cwd}/bin/cc-center-app statusline`;
    return h("section", { class: "block calm" },
      h("h4", null, "Plan"),
      h("p", { class: "calm-line" },
        "Claude Code reports the 5-hour and 7-day limits only to its status line. Route it through ",
        h("code", null, hook),
        " (add ", h("code", null, "-- <your current command>"),
        " to keep the one you have) and they show up here."));
  }
  const rl = u.rate_limits ?? {};
  const remote = u.host && u.host !== store.localHost ? ` · from ${u.host}` : "";
  return h("section", { class: "block" },
    h("h4", null, "Plan",
      h("span", { class: "muted" },
        `${u.model ? `${u.model} · ` : ""}as of ${ago(serverNow() - u.at)}${remote}`)),
    h("div", { class: "plan" },
      planRow("5-hour", rl.five_hour),
      planRow("7-day", rl.seven_day)));
}

/* -- sessions ----------------------------------------------------------- */

function resumeCommand(s: Session): string {
  const local = s.host === store.localHost;
  const cd = `cd ${s.cwd ?? "."} && claude --resume ${s.session_id}`;
  return local ? cd : `ssh -t ${s.host} '${cd}'`;
}

function attentionCard(s: Session): HTMLElement {
  const a = s.attention!;
  return h("article", { class: "alert", id: `agent-${s.session_id}` },
    h("div", { class: "alert-mark" }),
    h("div", { class: "alert-body" },
      h("h3", null, a.label, a.tool && h("span", { class: "tag" }, a.tool)),
      h("p", { class: "alert-title" }, s.title),
      h("div", { class: "alert-meta" },
        h("code", null, basename(s.cwd)),
        h("span", { class: "muted" }, s.host),
        h("span", { class: "muted" }, `stopped ${ago(secondsSince(a.since))}`)),
      ctxMeter(s),
      h("div", { class: "row-actions" },
        h("button", { class: "btn", type: "button", data: { copy: resumeCommand(s) } },
          "Copy resume command"),
        h("button", { class: "btn ghost", type: "button", data: { focusSession: s.session_id } },
          "Open record"),
        controls(s))));
}

/*
 * Reaching into a session. Interrupt is Ctrl+C: this turn stops, the
 * conversation stays. End is the process: it exits, and the resume command
 * still works afterwards -- so End asks first, Interrupt does not. Only
 * offered when a claude process is known to be there; with alive unknown
 * there is nothing to send to.
 */
function controls(s: Session): HTMLElement | null {
  if (s.alive !== true) return null;
  const send = async (signal: "INT" | "TERM", btn: HTMLButtonElement) => {
    if (signal === "TERM"
        && !confirm(`End this session? claude exits; you can resume it later.\n\n${s.title}`)) return;
    btn.disabled = true;
    try {
      const res = await api.signalSession({ host: s.host, session_id: s.session_id, signal });
      toast(signal === "INT" ? `Interrupted (pid ${res.pid})` : `Ended (pid ${res.pid})`);
    } catch (err) {
      toast(err instanceof Error ? err.message : "Could not signal the session");
    } finally {
      btn.disabled = false;
    }
  };
  const interrupt = h("button", { class: "btn ghost tiny", type: "button",
                                  title: "Send Ctrl+C: stop what it is doing now, keep the conversation" },
                      "Interrupt") as HTMLButtonElement;
  const end = h("button", { class: "btn ghost tiny danger", type: "button",
                            title: "End the claude process; the session can be resumed later" },
                "End") as HTMLButtonElement;
  interrupt.addEventListener("click", () => void send("INT", interrupt));
  end.addEventListener("click", () => void send("TERM", end));
  return h("span", { class: "controls" }, interrupt, end);
}

function runningRow(s: Session): HTMLElement {
  const tail = s.tail;
  const label = tail?.state === "tool"
    ? `running ${tail.tool ?? "a tool"}`
    : tail?.state === "waiting" ? "idle, last spoke" : "working";
  return h("article", { class: "run", id: `agent-${s.session_id}` },
    h("span", { class: `pulse ${s.alive === true ? "on" : "unknown"}`,
                title: s.alive === true ? "A claude process is running in this folder"
                  : "Could not check this machine for a claude process" }),
    h("div", { class: "run-body" },
      h("p", { class: "run-title" }, s.title),
      h("div", { class: "run-meta" },
        h("code", null, basename(s.cwd)),
        h("span", { class: "muted" }, s.host),
        h("span", { class: "muted" }, `${label} · ${ago(secondsSince(tail?.at))}`)),
      ctxMeter(s)),
    controls(s),
    h("button", { class: "btn ghost", type: "button", data: { copy: resumeCommand(s) } },
      "Resume"));
}

export function renderAgents(host: HTMLElement): void {
  const report = store.report;
  if (!report) return;

  const needing = report.sessions.filter((s) => s.attention);
  const window = store.settings.live_window ?? 600;
  /*
   * Both halves are required. The process answers "is anything still able to
   * continue this?", but it is matched by cwd, so every session that ever ran
   * in a folder inherits the one process living there -- on its own it would
   * call a session from yesterday live. Recency picks out which of them is
   * actually the one moving. alive === null means the machine could not be
   * reached, and there we fall back to the transcript rather than hide it.
   */
  const live = report.sessions
    .filter((s) => !s.attention && s.alive !== false && secondsSince(s.tail?.at) < window)
    .sort((a, b) => epoch(b.tail?.at) - epoch(a.tail?.at));

  const badge = document.getElementById("count-agents");
  if (badge) badge.textContent = needing.length ? String(needing.length) : "";
  document.title = needing.length ? `(${needing.length}) cc-center` : "cc-center";

  const machines = h("section", { class: "block" },
    h("h4", null, "Machines"),
    h("div", { class: "machine-grid" },
      report.hosts.map((state) => {
        const disabled = (store.settings.disabled_hosts ?? []).includes(state.host);
        const cls = state.busy ? "busy" : state.status === "ok" ? "ok" : "bad";
        return h("div", { class: `machine ${disabled ? "off" : ""}` },
          h("span", { class: `pulse ${cls}` }),
          h("span", { class: "machine-name" }, state.host),
          state.local && h("span", { class: "tag" }, "here"),
          h("span", { class: "muted" },
            state.status === "ok"
              ? `${state.n ?? 0} sessions · ${state.ms ?? 0} ms`
              : state.error || "unreachable"),
          state.procs !== null && state.procs !== undefined
            ? h("span", { class: "muted" }, `${state.procs} claude running`)
            : h("span", { class: "muted" }, "process state unknown"));
      })));

  mount(host,
    needing.length
      ? h("section", { class: "block" },
          h("h4", null, "Waiting on you"),
          needing.map(attentionCard))
      : h("section", { class: "block calm" },
          h("h4", null, "Waiting on you"),
          h("p", { class: "calm-line" }, "Nothing is stopped. Every agent is either working or finished.")),
    planBlock(),
    live.length
      ? h("section", { class: "block" },
          h("h4", null, "Working now",
            h("span", { class: "muted" }, `active in the last ${duration(window)}`)),
          live.map(runningRow))
      : null,
    machines);
}

export async function refreshNow(hosts?: string[]): Promise<void> {
  await api.refresh(hosts);
}

export { clock };
