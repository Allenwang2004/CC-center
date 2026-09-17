/**
 * Every CLAUDE.md in one place: the global one on each machine, then one per
 * project. They are edited where Claude Code reads them -- the file is the
 * truth, there is no copy -- so the list is read fresh from disk each time the
 * tab opens, over ssh for projects on other machines.
 *
 * A project without one gets a Create button rather than an empty editor, so
 * nothing is written until you actually write something.
 */

import { api } from "../core/api.js";
import { basename, plural } from "../core/format.js";
import { store } from "../core/store.js";
import { h, mount } from "../ui/dom.js";
import { deferWhileWriting, editor } from "../ui/editor/editor.js";
import type { ClaudeFile } from "../core/types.js";

let files: ClaudeFile[] | null = null;
let loading = false;
let error: string | null = null;
let query = "";
/** Files that do not exist yet but have an editor open. */
const creating = new Set<string>();

const keyOf = (f: ClaudeFile): string => `claudemd:${f.host}:${f.cwd ?? "~"}`;

export function setClaudeQuery(q: string): void {
  query = q.trim().toLowerCase();
}

/** Read the list from disk again and redraw. */
export async function loadClaudeFiles(host: HTMLElement): Promise<void> {
  loading = true;
  error = null;
  renderClaude(host);
  try {
    const res = await api.claudeFiles();
    files = res.files ?? [];
  } catch (err) {
    error = err instanceof Error ? err.message : "Could not read the files";
  } finally {
    loading = false;
    renderClaude(host);
  }
}

function card(f: ClaudeFile): HTMLElement {
  const key = keyOf(f);
  const isGlobal = f.scope === "global";
  const remote = f.host !== store.localHost;
  const name = isGlobal ? "Global" : basename(f.cwd);
  const open = f.exists || creating.has(key);

  const head = h("header", { class: "claude-head" },
    h("h3", null, name),
    remote ? h("span", { class: "tag remote" }, f.host) : h("span", { class: "tag" }, "this machine"),
    f.exists ? null : h("span", { class: "tag quiet" }, "not created yet"),
    f.error ? h("span", { class: "tag gone" }, f.error) : null,
    h("code", { class: "project-path", title: f.path }, f.path));

  let body: HTMLElement;
  if (open) {
    const ed = editor({
      key,
      kind: "file",
      cwd: f.cwd ?? "",
      host: f.host,
      ref: f.path,
      rows: isGlobal ? 14 : 10,
      saved: f.exists ? f.body : "",
      markdown: true,
      placeholder: isGlobal
        ? "Rules for every project on this machine: tone, tools, what never to do…"
        : "What Claude should know about this project: how to run it, its conventions, what to leave alone…",
      save: async (text) => {
        const res = await api.saveClaudeFile({ host: f.host, cwd: f.cwd, text });
        f.exists = true;
        f.body = text;
        creating.delete(key);
        return { path: res.path };
      },
    });
    if (f.exists) ed.sync(f.body);
    body = ed.el;
  } else {
    const create = h("button", { class: "btn ghost", type: "button" },
      isGlobal ? "Create ~/.claude/CLAUDE.md" : "Create .claude/CLAUDE.md");
    create.addEventListener("click", () => {
      creating.add(key);
      const pane = document.getElementById("pane-claude-body");
      if (pane) renderClaude(pane);
      (document.querySelector(`[data-editor="${CSS.escape(key)}"] textarea`) as HTMLElement | null)?.focus();
    });
    body = h("div", { class: "claude-create" },
      h("p", { class: "muted" },
        f.error ? `Could not read ${f.path}.` : "No file here yet. Claude Code reads it if it exists."),
      f.error ? null : create);
  }
  return h("article", { class: `claude-file${open ? " is-open" : ""}`, data: { key } }, head, body);
}

export function renderClaude(host: HTMLElement): void {
  // A scan pushes an update every few seconds while Claude Code is busy; the
  // pane must not be rebuilt around a file you are in the middle of editing.
  if (deferWhileWriting(host, renderClaude)) return;
  const meta = document.getElementById("claude-meta");
  if (loading && !files) {
    mount(host, h("div", { class: "empty" },
      h("h3", null, "Reading…"),
      h("p", null, "Local files are instant; each remote machine is one ssh round trip.")));
    if (meta) meta.textContent = "";
    return;
  }
  if (error && !files) {
    mount(host, h("div", { class: "empty" }, h("h3", null, "Could not read the files"), h("p", null, error)));
    if (meta) meta.textContent = "";
    return;
  }
  const all = files ?? [];
  const globals = all.filter((f) => f.scope === "global");
  const projects = all.filter((f) => f.scope === "project" && (
    !query || basename(f.cwd).toLowerCase().includes(query)
      || (f.cwd ?? "").toLowerCase().includes(query) || f.host.toLowerCase().includes(query)));

  if (meta) {
    const have = all.filter((f) => f.exists).length;
    meta.textContent = `${plural(all.length, "file")} · ${have} exist`
      + (loading ? " · reading…" : "");
  }

  mount(host,
    h("section", { class: "claude-group" },
      h("h2", null, "Global",
        h("span", { class: "muted" }, "~/.claude/CLAUDE.md on each machine, read before every project")),
      ...globals.map(card)),
    h("section", { class: "claude-group" },
      h("h2", null, "Projects",
        h("span", { class: "muted" }, ".claude/CLAUDE.md inside the project folder")),
      projects.length
        ? projects.map(card)
        : h("div", { class: "empty" },
            h("h3", null, query ? "No project matches" : "No projects"),
            h("p", null, query ? "Try another name." : "Run Claude Code somewhere and it will show up here."))));
}
