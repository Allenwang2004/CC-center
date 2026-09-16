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
globalThis.FileReader = window.FileReader;
globalThis.File = window.File;
globalThis.CSS = window.CSS ?? { escape: (s) => s.replace(/["\\]/g, "\\$&") };
globalThis.getComputedStyle = window.getComputedStyle.bind(window);
window.CC_TOKEN = "test";

// CLAUDE.md files are read from disk, not from the report, so the fixture
// is a small hand-made list: one global that exists, one project without.
const CLAUDE_FILES = { local_host: state.local_host, files: [
  { scope: "global", host: state.local_host, cwd: null, path: "/Users/x/.claude/CLAUDE.md",
    exists: true, body: "# Global\n\nBe terse.\n", mtime: 1, error: null, last_active: 0 },
  { scope: "project", host: state.local_host, cwd: "/Users/x/proj", path: "/Users/x/proj/.claude/CLAUDE.md",
    exists: false, body: "", mtime: null, error: null, last_active: 2 },
] };

// One transparent pixel, the way the server hands a picture back.
const PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==";
const IMAGE_ID = "20260917-120000-0badf00d.png";

const seen = [];
const posts = [];
globalThis.fetch = async (url, opts) => {
  seen.push(String(url));
  if (opts?.body) posts.push([String(url), JSON.parse(opts.body)]);
  const body =
    String(url).startsWith("/api/state") ? state :
    String(url).startsWith("/api/report") ? report :
    String(url).startsWith("/api/claudemd") ? CLAUDE_FILES :
    String(url).startsWith("/api/image?") ? { ok: true, type: "image/png", data: PNG_B64 } :
    String(url) === "/api/image" ? { ok: true, id: IMAGE_ID, url: `cc://image/${IMAGE_ID}` } :
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
for (const name of ["agents", "projects", "sessions", "activity", "claude", "settings"]) {
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
 * Claude.md: the global file opens in an editor, the project without one gets
 * a Create button, and pressing it opens an empty editor in its place.
 */
{
  const tab = d.querySelector('#tabs button[data-pane="claude"]');
  tab.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 300));
  const editors = d.querySelectorAll("#pane-claude-body .editor");
  const create = d.querySelector("#pane-claude-body .claude-create button");
  console.log("\nclaude.md files :", d.querySelectorAll("#pane-claude-body .claude-file").length,
              "| editors", editors.length, "| create button", Boolean(create));
  if (editors.length !== 1 || !create) errors.push("claude.md pane did not render one editor and one create button");
  create?.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 100));
  const after = d.querySelectorAll("#pane-claude-body .editor").length;
  console.log("after create    :", after, "editors");
  if (after !== 2) errors.push("Create did not open an editor");
  const area = d.querySelectorAll("#pane-claude-body .editor textarea.writing")[1];
  area.value = "# proj\n";
  area.dispatchEvent(new window.Event("input", { bubbles: true }));
  area.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", metaKey: true, bubbles: true }));
  await new Promise((r) => setTimeout(r, 250));
  const saved = posts.filter(([u]) => u.startsWith("/api/claudemd"));
  console.log("claude.md save  :", JSON.stringify(saved));
  if (!saved.length || saved[0][1].cwd !== "/Users/x/proj") errors.push("saving a CLAUDE.md did not POST /api/claudemd");
}

/*
 * Settings live on their own pane now, found by id rather than by where they
 * sit -- so every field has to be present and carry the server's value, and
 * the sidebar must have let go of them.
 */
const fields = ["local_poll", "remote_poll", "remote_poll_hot", "live_window",
                "notify_waiting_after", "notify_tool_after", "browser",
                "notify_scope", "sidechains", "oneshot", "notify_sound"];
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
if (d.getElementById("account-email").textContent !== state.auth.email) errors.push("account email not shown");
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

/*
 * The journal is one day at a time: a list of days down the left (today is
 * always there), one editor on the right, and picking another day swaps the
 * editor for that day's without losing the first one's unsaved text.
 */
{
  const shelf = d.querySelector(".journal");
  const items = [...shelf.querySelectorAll(".journal-index .journal-item")];
  const editorsBefore = shelf.querySelectorAll("textarea.writing").length;
  const openKey = () => shelf.querySelector(".journal-open [data-editor]")?.dataset.editor;
  const first = openKey();
  console.log("\njournal days    :", items.length, "| one editor open:", editorsBefore === 1,
              "| today listed:", items.some((x) => x.textContent.includes("today")));
  if (editorsBefore !== 1) errors.push("journal must show exactly one editor");
  if (!items.some((x) => x.textContent.includes("today"))) errors.push("journal list has no today");
  const other = items.find((x) => !x.classList.contains("is-on"));
  if (other) {
    shelf.querySelector("textarea.writing").value = "kept while away";
    shelf.querySelector("textarea.writing").dispatchEvent(new window.Event("input", { bubbles: true }));
    other.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 150));
    const shelf2 = d.querySelector(".journal");
    const second = shelf2.querySelector(".journal-open [data-editor]")?.dataset.editor;
    console.log("journal switch  :", first, "->", second);
    if (!second || second === first) errors.push("picking another day did not switch the journal");
    const back = [...shelf2.querySelectorAll(".journal-item")].find((x) => !x.classList.contains("is-on"));
    back.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 150));
    const kept = d.querySelector(".journal textarea.writing").value === "kept while away";
    console.log("journal draft   :", kept ? "kept across the switch" : "LOST");
    if (!kept) errors.push("switching days lost an unsaved journal draft");
  }
}

/*
 * The preview is more than the renderer: fences get colour and a Copy button,
 * $..$ is typeset, headings get anchors, and a full preview with a few
 * headings grows an outline down the side. The libraries come from
 * web/vendor, so a stale vendor/ shows up here.
 */
{
  const shelf = d.querySelector(".note-open .editor");
  const area = shelf.querySelector("textarea.writing");
  area.value = "# One\n\n```py\ndef f(x):\n    return x\n```\n\n## Two\n\nInline $E=mc^2$ here.\n\n## Three";
  area.dispatchEvent(new window.Event("input", { bubbles: true }));
  shelf.querySelector('.md-mode[data-mode="preview"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 600));
  const pv = shelf.querySelector(".md-preview");
  const coloured = pv.querySelectorAll("code .hljs-keyword").length;
  const typeset = pv.querySelectorAll(".math.is-typeset .katex").length;
  const copy = pv.querySelectorAll(".code-block .code-copy").length;
  const anchors = pv.querySelectorAll("h1 .anchor, h2 .anchor").length;
  const outlineLinks = shelf.querySelectorAll(".md-outline a").length;
  console.log("\npreview code    :", coloured, "keywords coloured |", copy, "copy button");
  console.log("preview math    :", typeset, "typeset");
  console.log("preview outline :", anchors, "anchors |", outlineLinks, "outline links |",
              "outline shown:", shelf.dataset.outline === "1");
  if (!coloured) errors.push("code block was not highlighted (is web/vendor/hljs.js built?)");
  if (!typeset) errors.push("math was not typeset (is web/vendor/katex.js built?)");
  if (copy !== 1) errors.push("code block has no Copy button");
  if (anchors !== 3) errors.push("headings did not get anchors");
  if (outlineLinks !== 3 || shelf.dataset.outline !== "1") errors.push("outline did not list the headings");
  shelf.querySelector('.md-mode[data-mode="write"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  area.value = "";
  area.dispatchEvent(new window.Event("input", { bubbles: true }));
}

/*
 * A screenshot pasted into the journal goes up through the server and comes
 * back as cc://image/<id> in the text; the preview then asks the server for
 * the bytes and shows them. Notes do not take pictures at all.
 */
{
  const journal = d.querySelector(".journal .editor");
  const area = journal.querySelector("textarea.writing");
  const png = new window.File([Uint8Array.from(Buffer.from(PNG_B64, "base64"))], "shot.png", { type: "image/png" });
  area.value = "before ";
  area.setSelectionRange(7, 7);
  const paste = new window.Event("paste", { bubbles: true, cancelable: true });
  paste.clipboardData = { files: [png], items: [] };
  area.dispatchEvent(paste);
  const placeholder = area.value.includes("![Uploading 1…]()");
  await new Promise((r) => setTimeout(r, 300));
  const upload = posts.find(([u]) => u === "/api/image");
  console.log("\nimage paste     :", "placeholder shown:", placeholder,
              "| uploaded:", Boolean(upload), "| text:", JSON.stringify(area.value));
  if (!placeholder) errors.push("pasting an image did not show a placeholder");
  if (!upload || !upload[1].data) errors.push("pasting an image did not POST /api/image");
  if (area.value !== `before ![shot](cc://image/${IMAGE_ID})`) errors.push("upload did not replace the placeholder");
  journal.querySelector('.md-mode[data-mode="preview"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 300));
  const img = journal.querySelector(".md-preview img.cc-image");
  console.log("image preview   :", img ? `src ${img.getAttribute("src")?.slice(0, 22)}…` : "NO IMG",
              "| fetched:", seen.some((u) => u.startsWith("/api/image?id=")));
  if (!img || !img.getAttribute("src")?.startsWith("data:image/png;base64,")) errors.push("the pasted image was not resolved in the preview");
  journal.querySelector('.md-mode[data-mode="write"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  area.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));

  const note = d.querySelector(".note-open .editor");
  const noteArea = note.querySelector("textarea.writing");
  const before = noteArea.value;
  const paste2 = new window.Event("paste", { bubbles: true, cancelable: true });
  paste2.clipboardData = { files: [png], items: [] };
  noteArea.dispatchEvent(paste2);
  console.log("note paste      :", noteArea.value === before ? "ignored, as it should be" : "TOOK THE IMAGE",
              "| img button on journal only:", Boolean(journal.querySelector('.md-tool[title^="Add an image"]')) && !note.querySelector('.md-tool[title^="Add an image"]'));
  if (noteArea.value !== before) errors.push("a note accepted a pasted image");
}

console.log("\nerrors          :", errors.length ? errors : "none");

process.exit(errors.length ? 1 : 0);
