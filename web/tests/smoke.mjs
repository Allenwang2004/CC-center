// Renders the real front end against a real payload in jsdom, so a crash in a
// view shows up here rather than in the browser. Capture the payloads first:
//   TOK=$(curl -s localhost:8787/ | grep -oE 'CC_TOKEN = "[^"]+"' | cut -d\" -f2)
//   curl -sH "X-CC-Token: $TOK" localhost:8787/api/state  > /tmp/state.json
//   curl -sH "X-CC-Token: $TOK" localhost:8787/api/report > /tmp/report.json
// then: npm run smoke
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const WEB = new URL("..", import.meta.url).pathname.replace(/\/$/, "");
const state = JSON.parse(readFileSync(process.env.CC_STATE_JSON ?? "/tmp/state.json", "utf8"));
const report = JSON.parse(readFileSync(process.env.CC_REPORT_JSON ?? "/tmp/report.json", "utf8"));

// A payload captured before the sign-in gate has no `auth`; a signed-in one
// is what the walk below needs. The locked state is exercised separately.
const SIGNED = { configured: true, project: "test.supabase.co", signed_in: true,
                 email: "you@example.com", synced_at: Date.now() / 1000 - 90,
                 pending: 0, error: null };
state.auth = state.auth?.signed_in ? state.auth : SIGNED;
report.auth = report.auth ?? state.auth;
report.store = { synced_at: SIGNED.synced_at, pending: 0, ...(report.store ?? {}) };
state.report = report;

const html = readFileSync(`${WEB}/index.html`, "utf8")
  .replace("__CC_TOKEN__", "test")
  .replace(/<script type="module"[^>]*><\/script>/, "");

const dom = new JSDOM(html, { url: "http://127.0.0.1:8787/", pretendToBeVisual: true });
const { window } = dom;

const errors = [];
window.addEventListener("error", (e) => errors.push("window error: " + e.message));

globalThis.window = window;
globalThis.document = window.document;
Object.defineProperty(globalThis, "navigator", { value: window.navigator, configurable: true });
Object.defineProperty(globalThis, "location", { value: window.location, configurable: true });
globalThis.HTMLElement = window.HTMLElement;
globalThis.HTMLTextAreaElement = window.HTMLTextAreaElement;
globalThis.HTMLInputElement = window.HTMLInputElement;
globalThis.HTMLSelectElement = window.HTMLSelectElement;
globalThis.HTMLDetailsElement = window.HTMLDetailsElement;
globalThis.Node = window.Node;
globalThis.CSS = window.CSS ?? { escape: (s) => s.replace(/["\\]/g, "\\$&") };
globalThis.getComputedStyle = window.getComputedStyle.bind(window);
window.CC_TOKEN = "test";

const seen = [];
const posts = [];
globalThis.fetch = async (url, opts) => {
  seen.push(String(url));
  if (opts?.body) posts.push([String(url), JSON.parse(opts.body)]);
  const body =
    String(url).startsWith("/api/state") ? state :
    String(url).startsWith("/api/report") ? report :
    String(url).startsWith("/api/markdown") ? "# Report\n\nSome **text**.\n\n- a bullet\n" :
    { ok: true };
  return {
    ok: true,
    json: async () => body,
    text: async () => (typeof body === "string" ? body : JSON.stringify(body)),
  };
};
class FakeEventSource {
  constructor(u) { this.url = u; }
  addEventListener() {}
  close() {}
}
globalThis.EventSource = FakeEventSource;
window.EventSource = FakeEventSource;

// Locked first: with the server not signed in, the page is the form and
// nothing else is fetched.
let locked = true;
const realFetch = globalThis.fetch;
globalThis.fetch = async (url, opts) => {
  if (locked && opts?.body) posts.push([String(url), JSON.parse(opts.body)]);
  if (locked && String(url).startsWith("/api/state"))
    return { ok: true, json: async () => ({ auth: { ...SIGNED, signed_in: false, email: null },
                                            cwd: "/x", pid: 1, started: 0 }) };
  if (locked && String(url).startsWith("/api/auth/code"))
    return { ok: true, json: async () => ({ ok: true, email: JSON.parse(opts.body).email }) };
  if (locked && String(url).startsWith("/api/auth/verify")) {
    locked = false;
    return { ok: true, json: async () => ({ ok: true, auth: SIGNED }) };
  }
  return realFetch(url, opts);
};

await import(pathToFileURL(`${WEB}/dist/main.js`).href);
await new Promise((r) => setTimeout(r, 300));

const d = window.document;
{
  const shellHidden = d.getElementById("shell").hidden;
  const formShown = !d.getElementById("signin").hidden;
  const dataFetched = seen.some((u) => u.startsWith("/api/report") || u.startsWith("/api/events"));
  console.log("locked          :", "shell hidden", shellHidden, "| form shown", formShown,
              "| data fetched", dataFetched);
  if (!shellHidden || !formShown) errors.push("sign-in screen did not take over the page");
  if (dataFetched) errors.push("data was fetched before sign-in");

  // Email → code → verify, the way a person would.
  d.getElementById("signin-email").value = "you@example.com";
  d.getElementById("signin-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await new Promise((r) => setTimeout(r, 150));
  const codeStep = !d.getElementById("signin-step-code").hidden;
  console.log("code step       :", codeStep, "| sent to", d.getElementById("signin-sent-to").textContent);
  if (!codeStep) errors.push("sending the email did not move to the code step");
  d.getElementById("signin-code").value = "123 456";
  d.getElementById("signin-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await new Promise((r) => setTimeout(r, 400));
  const unlocked = !d.getElementById("shell").hidden && d.getElementById("signin").hidden;
  console.log("signed in       :", unlocked,
              "| verify posted", JSON.stringify(posts.find(([u]) => u.startsWith("/api/auth/verify"))?.[1]));
  if (!unlocked) errors.push("verifying the code did not open the page");
}
const outline = (pane) => {
  const el = d.getElementById(`pane-${pane}-body`);
  if (!el) return `${pane}: MISSING CONTAINER`;
  // A tab the script does not know about leaves its pane hidden -- a stale
  // dist/ against a newer index.html looks exactly like this.
  if (d.getElementById(`pane-${pane}`)?.hidden) {
    errors.push(`${pane}: pane still hidden after clicking its tab`);
    return `${pane}: STILL HIDDEN`;
  }
  const text = (el.textContent || "").replace(/\s+/g, " ").trim();
  return `${pane}: ${el.children.length} blocks, ${text.length} chars :: ${text.slice(0, 150)}`;
};

// Walk every pane the way a person would.
const { default: _ } = { default: null };
for (const name of ["agents", "projects", "sessions", "activity", "report", "settings"]) {
  const btn = d.querySelector(`#tabs button[data-pane="${name}"]`);
  btn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 250));
  console.log(outline(name));
}

/*
 * The plan's limits and each session's context are the two meters on the
 * Agents tab. Both come from the payload, so with a usage snapshot present the
 * block must render two rows, and every live session with a ctx must show one.
 */
{
  const agents = d.querySelector('#tabs button[data-pane="agents"]');
  agents.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 200));
  const rows = d.querySelectorAll("#pane-agents-body .plan-row");
  const withCtx = report.sessions.filter((x) => x.ctx && x.ctx.size).length;
  console.log("\nplan block      :", report.usage ? `${rows.length} rows` : "no snapshot -> hint shown",
    report.usage ? (rows.length === 2 ? "" : "EXPECTED 2") : (d.querySelector("#pane-agents-body .calm code") ? "" : "HINT MISSING"));
  console.log("plan meters     :", [...rows].map((r) => r.querySelector(".plan-pct")?.textContent).join(" "));
  console.log("ctx meters      :", d.querySelectorAll("#pane-agents-body .ctx").length,
    "shown |", withCtx, "sessions carry ctx");
  if (report.usage && rows.length !== 2) errors.push("plan block did not render both windows");
}

/*
 * Spend is the one view that ignores the range: one cell per day on record,
 * every day in the table, and the readout follows the cell under the pointer.
 */
{
  const activity = d.querySelector('#tabs button[data-pane="activity"]');
  activity.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 200));
  const days = Object.keys(report.daily || {}).sort();
  const cells = d.querySelectorAll("#pane-activity-body button.spend-cell");
  const coloured = d.querySelectorAll("#pane-activity-body button.spend-cell:not(.l0)");
  const rows = d.querySelectorAll("#pane-activity-body .spend-table tbody tr");
  console.log("\nspend days      :", days.length, "on record |", cells.length, "cells |", coloured.length, "coloured |", rows.length, "table rows");
  if (days.length && rows.length !== days.length) errors.push("spend table does not list every day");
  if (days.length && coloured.length !== days.length) errors.push("spend calendar colours a different number of days than are on record");
  const firstCell = [...cells].find((c) => c.dataset.day === days[0]);
  firstCell?.dispatchEvent(new window.MouseEvent("mouseenter"));
  const readout = d.querySelector("#pane-activity-body .spend-readout")?.textContent ?? "";
  console.log("spend readout   :", readout.slice(0, 90));
  if (days.length && !readout.includes("$")) errors.push("spend readout shows no cost");
  console.log("range stats     :", d.querySelectorAll("#pane-activity-body .stat").length, "tiles after the calendar");
}

/*
 * Settings live on their own pane now, found by id rather than by where they
 * sit -- so every field has to be present and carry the server's value, and
 * the sidebar must have let go of them.
 */
const fields = ["local_poll", "remote_poll", "remote_poll_hot", "live_window", "prompts",
                "notify_waiting_after", "notify_tool_after", "out_dir", "browser",
                "notify_scope", "sidechains", "oneshot", "tokens", "notify_sound"];
const filled = fields.filter((id) => {
  const el = d.getElementById(id);
  if (!el) return false;
  const want = state.settings[id];
  return el.type === "checkbox" ? el.checked === Boolean(want) : el.value === String(want);
});
console.log("\nsettings filled :", filled.length, "of", fields.length,
  filled.length === fields.length ? "" : "MISSING " + fields.filter((f) => !filled.includes(f)).join(","));
console.log("settings in pane:", fields.every((id) => d.getElementById(id)?.closest("#pane-settings")));
console.log("account         :", d.getElementById("account-email").textContent, "|",
            d.getElementById("account-sync").textContent);
if (d.getElementById("account-email").textContent !== SIGNED.email) errors.push("account email not shown");
console.log("sidebar groups  :", [...d.querySelectorAll(".sidebar .group h2")].map((x) => x.firstChild.textContent.trim()).join(", "));
console.log("sidebar machines:", d.querySelectorAll("#machines .machine-row").length);
console.log("range chips     :", [...d.querySelectorAll("#range .chip")].map((b) => b.textContent).join(" "));
console.log("connection      :", (d.getElementById("connection").textContent || "").trim());
console.log("scope           :", d.getElementById("scope").textContent);
console.log("editors         :", d.querySelectorAll("textarea.writing").length);
console.log("project blocks  :", d.querySelectorAll("details.project").length);
console.log("fetches         :", [...new Set(seen)].join(", "));
// The interaction that has to feel right: type a note, look away, it is saved.
const projects = d.querySelector('#tabs button[data-pane="projects"]');
projects.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise((r) => setTimeout(r, 200));

/*
 * The two halves have to stay apart: Projects carries only what you wrote,
 * and everything the tool computed -- the day's changes and the question by
 * question ledger -- belongs to Sessions.
 */
console.log("\nprojects: turns      :", d.querySelectorAll("#pane-projects-body .entry:not(.loose)").length);
console.log("projects: summaries  :", d.querySelectorAll("#pane-projects-body .summary").length);
const sessTab = d.querySelector('#tabs button[data-pane="sessions"]');
sessTab.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise((r) => setTimeout(r, 250));
// A <details> starts as the store remembers it -- nothing here was opened yet,
// so none of them may come back open. (`open: false` written as an attribute
// would read as open, and every card would render expanded.)
console.log("sessions: start open :",
  d.querySelectorAll("#pane-sessions-body details.session[open]").length,
  "of", d.querySelectorAll("#pane-sessions-body details.session").length, "cards");
d.querySelectorAll("details.session").forEach((x) => { x.open = true; });
console.log("sessions: turns      :", d.querySelectorAll("#pane-sessions-body .entry").length);
console.log("sessions: summaries  :", d.querySelectorAll("#pane-sessions-body .summary").length);
/*
 * Every command a turn ran gets its own row, and opening one holds the whole
 * command line -- so a heredoc is readable instead of being cut at the column
 * with the rest hidden in a tooltip.
 */
const cmds = [...d.querySelectorAll("#pane-sessions-body details.cmd:not(.more)")];
const full = cmds.map((x) => x.querySelector("pre")?.textContent ?? "");
console.log("sessions: commands   :", cmds.length,
  "| longest opens to", Math.max(0, ...full.map((c) => c.length)), "chars");
console.log("sessions: cmd panels  :", cmds.filter((x) => x.open).length, "open by default");
console.log("sessions: cut short  :",
  full.filter((c, i) => cmds[i].querySelector("summary code").textContent !== c).length,
  "of them are shortened on the row");
projects.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise((r) => setTimeout(r, 250));

// Notes are a two-pane index + editor, so check the index renders and that the
// blank "new note" form is what a project with no notes opens on.
console.log("\nnote panes      :", d.querySelectorAll(".note-pane").length);
console.log("note index rows :", d.querySelectorAll(".note-index .note-item").length);
console.log("new-note button :", Boolean(d.querySelector(".note-item-new")));

const shelf = d.querySelector(".note-open .editor");
const composer = shelf.querySelector("textarea.writing");
const titleBox = shelf.querySelector("input.note-title-input");
// A note is just text; its name is its first line, so no title field is shown.
console.log("composer found  :", Boolean(composer),
            "| no title field shown:", !titleBox || titleBox.hidden);

composer.value = "Trying the save path.";
composer.dispatchEvent(new window.Event("input", { bubbles: true }));
console.log("state after typing:", shelf.querySelector(".editor-state").textContent);

/*
 * Typing Chinese, the Enter that accepts an IME candidate also arrives as a
 * keydown. If the editor acts on it, one press both confirms the character and
 * carries on the list -- so a composing Enter has to change nothing.
 */
const press = (key, isComposing) =>
  composer.dispatchEvent(new window.KeyboardEvent("keydown",
    { key, isComposing, bubbles: true, cancelable: true }));
composer.value = "- \u7b2c\u4e00\u9805";
composer.setSelectionRange(composer.value.length, composer.value.length);
composer.dispatchEvent(new window.Event("input", { bubbles: true }));
press("Enter", true);
console.log("ime enter is inert :", composer.value === "- \u7b2c\u4e00\u9805");
press("Enter", false);
console.log("real enter works   :", composer.value === "- \u7b2c\u4e00\u9805\n- ");
composer.value = "Trying the save path.";
composer.dispatchEvent(new window.Event("input", { bubbles: true }));

// Nothing may be written on a timer -- waiting must produce no request at all.
await new Promise((r) => setTimeout(r, 1300));
console.log("no autosave     :", posts.filter(([u]) => u.startsWith("/api/entry")).length === 0);

const saveBtn = shelf.querySelector("button.save");
console.log("save button     :", Boolean(saveBtn), "| shown while dirty:", saveBtn && !saveBtn.hidden);
saveBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise((r) => setTimeout(r, 250));

const saves = posts.filter(([u]) => u.startsWith("/api/entry"));
console.log("save on click   :", JSON.stringify(saves));
console.log("state after save:", shelf.querySelector(".editor-state").textContent);
console.log("composer cleared:", composer.value === "");
console.log("no title sent   :", saves.every(([, b]) => b.title === undefined));

// Cmd+Enter should commit without waiting for the debounce.
const journal = d.querySelector(".journal textarea.writing");
journal.value = "Why I made these changes.";
journal.dispatchEvent(new window.Event("input", { bubbles: true }));
journal.dispatchEvent(new window.KeyboardEvent("keydown",
  { key: "Enter", metaKey: true, bubbles: true }));
await new Promise((r) => setTimeout(r, 250));
const journalSaves = posts.filter(([u, b]) => u.startsWith("/api/entry") && b.kind === "journal");
console.log("journal save    :", JSON.stringify(journalSaves));

console.log("\nerrors          :", errors.length ? errors : "none");

process.exit(errors.length ? 1 : 0);
