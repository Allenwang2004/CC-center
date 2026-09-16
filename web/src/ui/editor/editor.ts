/**
 * The writing surface for notes and journal entries.
 *
 * Nothing is written until you say so. An editor that saved on a timer wrote a
 * note out of a stray keystroke and committed half-finished sentences, so the
 * commit is explicit: "Save now", or ⌘↩. The editor owns its own DOM and is
 * held by `keep()`, so unsaved text survives a background refresh and survives
 * clicking to another note and back -- and `unsaved()` lets the page warn you
 * before the tab closes on top of it.
 */

import { api } from "../../core/api.js";
import { fileToBase64 } from "../../core/images.js";
import { composing, h, keep } from "../dom.js";
import { enhance, outline } from "./enhance.js";
import { markdownToHtml } from "./markdown.js";
import {
  codeBlock, continueList, footnote, heading, indent, link, prefixLines, spoiler, table,
  wrap, type Edit, type Sel,
} from "./syntax.js";

/** "file" is a plain file on disk (a CLAUDE.md): saved through `save`, never deleted here. */
type Kind = "journal" | "note" | "file";
type Status = "clean" | "dirty" | "saving" | "saved" | "failed";

/** Every editor currently holding unsaved text, so the page can warn on unload. */
const dirtyEditors = new Set<string>();

export const unsavedCount = (): number => dirtyEditors.size;

export interface EditorOptions {
  key: string;
  kind: Kind;
  cwd: string;
  host: string;
  /** journal: the day. Note: the ref, or "" for the composer that opens new notes. */
  ref: string;
  placeholder: string;
  rows: number;
  saved: string;
  /** Full markdown surface: toolbar, shortcuts, and a live preview. */
  markdown?: boolean;
  /** Pictures can be pasted, dropped or picked; they go to the account's bucket. */
  images?: boolean;
  /** Notes get a title line of their own; journals are titled by their date. */
  withTitle?: boolean;
  savedTitle?: string;
  titlePlaceholder?: string;
  /** Composers clear themselves and hand the new ref back. */
  onCreated?: (ref: string) => void;
  onSaved?: () => void;
  /** Fired after the entry is gone, so the caller can move the selection. */
  onDeleted?: (ref: string) => void;
  /**
   * Where the text goes when it is not a note or a journal entry. Given this,
   * the editor never calls the entries API; the caller owns the write.
   */
  save?: (text: string) => Promise<{ path?: string | null; warn?: string | null }>;
}

export interface Editor {
  el: HTMLElement;
  /** Called when new server data arrives; ignored while you have unsaved work. */
  sync(savedText: string, savedTitle?: string): void;
  /** Put the caret where writing starts -- the title if there is one. */
  focus(): void;
  /** Drop text in as unsaved work, so a draft still has to be looked at. */
  replace(text: string): void;
}

/** `keep` stores elements, so the editor rides along on the node it owns. */
interface Carrier extends HTMLElement {
  __editor?: Editor;
}

export function editor(options: EditorOptions): Editor {
  const el = keep(options.key, () => {
    const made = build(options);
    (made.el as Carrier).dataset.editor = options.key;
    return made.el as Carrier;
  }) as Carrier;
  return el.__editor!;
}

function build(options: EditorOptions): Editor {
  const isComposer = options.kind === "note" && !options.ref;
  let ref = options.ref;
  let saved = options.saved;
  let savedTitle = options.savedTitle ?? "";
  let status: Status = "clean";
  let inFlight = false;

  const area = h("textarea", {
    class: "writing",
    rows: options.rows,
    placeholder: options.placeholder,
    spellcheck: false,
  });
  area.value = saved;

  const titleBox = h("input", {
    class: "writing note-title-input",
    type: "text",
    placeholder: options.titlePlaceholder ?? "Title",
    spellcheck: false,
    hidden: !options.withTitle,
  });
  titleBox.value = savedTitle;

  /* -- markdown surface -------------------------------------------------- */

  type Mode = "write" | "split" | "preview";
  let mode: Mode = "write";

  const preview = h("div", { class: "md-preview prose" });
  let side: HTMLElement = h("nav", { class: "md-outline", hidden: true });
  const paintPreview = (): void => {
    if (mode === "write") return;
    preview.innerHTML = markdownToHtml(area.value)
      || '<p class="muted">Nothing to preview yet.</p>';
    // Only the full preview has room for the outline; split is two columns already.
    const headings = enhance(preview);
    const next = outline(mode === "preview" ? headings : [], (id) =>
      preview.querySelector<HTMLElement>(`#${CSS.escape(id)}`)
        ?.scrollIntoView({ block: "start", behavior: "smooth" }));
    side.replaceWith(next);
    side = next;
    root.dataset.outline = next.hidden ? "" : "1";
  };

  const apply = (fn: (t: string, sel: Sel) => Edit): void => {
    const sel = { start: area.selectionStart, end: area.selectionEnd };
    const next = fn(area.value, sel);
    area.value = next.text;
    area.setSelectionRange(next.start, next.end);
    area.focus();
    onInput();
  };

  const tool = (label: string, hint: string, fn: (t: string, sel: Sel) => Edit): HTMLElement => {
    const b = h("button", { class: "md-tool", type: "button", title: hint }, label);
    // mousedown, not click: the textarea must keep its selection.
    b.addEventListener("mousedown", (e) => {
      e.preventDefault();
      apply(fn);
    });
    return b;
  };

  const modeBtn = (m: Mode, label: string): HTMLElement => {
    const b = h("button",
      { class: `md-mode${mode === m ? " is-on" : ""}`, type: "button", data: { mode: m } },
      label);
    b.addEventListener("click", () => {
      mode = m;
      root.dataset.mode = m;
      for (const other of tools.querySelectorAll(".md-mode"))
        other.classList.toggle("is-on", (other as HTMLElement).dataset.mode === m);
      paintPreview();
      if (m !== "preview") area.focus();
    });
    return b;
  };

  /* -- pictures ---------------------------------------------------------- */

  /**
   * A picture goes up before it goes in. The moment you paste, the entry gets
   * an `![Uploading…]()` where the caret was, so you can keep typing; when the
   * server answers, that placeholder becomes the real `![name](cc://image/…)`,
   * wherever it has moved to by then. If the upload fails, the placeholder is
   * taken back out and the bar says why.
   */
  let uploads = 0;
  const addImage = async (file: File): Promise<void> => {
    if (!options.images) return;
    if (!file.type.startsWith("image/")) {
      state.textContent = "Only images can be added.";
      return;
    }
    const alt = (file.name || "image").replace(/\.[a-z0-9]+$/i, "") || "image";
    const mark = `![Uploading ${++uploads}…]()`;
    const at = area.selectionStart;
    area.value = area.value.slice(0, at) + mark + area.value.slice(area.selectionEnd);
    area.setSelectionRange(at + mark.length, at + mark.length);
    onInput();
    try {
      const res = await api.uploadImage(await fileToBase64(file));
      area.value = area.value.replace(mark, `![${alt}](${res.url})`);
    } catch (err) {
      area.value = area.value.replace(mark, "");
      onInput();
      state.textContent = err instanceof Error ? err.message : "Could not upload the image";
      return;
    }
    onInput();
  };

  const picker = h("input", { type: "file", accept: "image/*", hidden: true });
  picker.addEventListener("change", () => {
    for (const f of Array.from(picker.files ?? [])) void addImage(f);
    picker.value = "";
  });
  const pickImage = h("button", { class: "md-tool", type: "button",
                                  title: "Add an image (or paste / drop one)" }, "img");
  pickImage.addEventListener("mousedown", (e) => {
    e.preventDefault();
    picker.click();
  });
  area.addEventListener("paste", (e) => {
    const files = Array.from(e.clipboardData?.files ?? [])
      .filter((f) => f.type.startsWith("image/"));
    if (!options.images || !files.length) return;
    e.preventDefault();
    for (const f of files) void addImage(f);
  });
  area.addEventListener("dragover", (e) => {
    if (options.images && e.dataTransfer?.types.includes("Files")) e.preventDefault();
  });
  area.addEventListener("drop", (e) => {
    const files = Array.from(e.dataTransfer?.files ?? []);
    if (!options.images || !files.length) return;
    e.preventDefault();
    for (const f of files) void addImage(f);
  });

  /*
   * The buttons are labelled with the syntax they insert, not with icons. These
   * notes are markdown files that live in the project next to the code, so the
   * toolbar doubles as the legend for what you are actually typing -- and it
   * stays in mono, where every other machine-printed label in this tool lives.
   */
  const tools = h("div", { class: "md-tools", hidden: !options.markdown },
    tool("#", "Heading 1", (t, sel) => heading(t, sel, 1)),
    tool("##", "Heading 2", (t, sel) => heading(t, sel, 2)),
    tool("###", "Heading 3", (t, sel) => heading(t, sel, 3)),
    h("span", { class: "md-sep" }),
    tool("**", "Bold  ⌘B", (t, sel) => wrap(t, sel, "**")),
    tool("*", "Italic  ⌘I", (t, sel) => wrap(t, sel, "*")),
    tool("~~", "Strikethrough", (t, sel) => wrap(t, sel, "~~")),
    tool("==", "Highlight", (t, sel) => wrap(t, sel, "==")),
    tool("++", "Underline", (t, sel) => wrap(t, sel, "++")),
    tool("^", "Superscript", (t, sel) => wrap(t, sel, "^")),
    tool("~", "Subscript", (t, sel) => wrap(t, sel, "~")),
    tool("`", "Code", (t, sel) => wrap(t, sel, "`")),
    h("span", { class: "md-sep" }),
    tool("-", "Bullet list", (t, sel) => prefixLines(t, sel, "- ")),
    tool("1.", "Numbered list", (t, sel) => prefixLines(t, sel, "", true)),
    tool("- [ ]", "Task list", (t, sel) => prefixLines(t, sel, "- [ ] ")),
    tool(">", "Quote", (t, sel) => prefixLines(t, sel, "> ")),
    h("span", { class: "md-sep" }),
    tool("[]()", "Link  ⌘K", (t, sel) => link(t, sel)),
    tool("```", "Code block", (t, sel) => codeBlock(t, sel)),
    tool("|", "Table", (t, sel) => table(t, sel)),
    tool("---", "Divider", (t, sel) => ({
      text: t.slice(0, sel.start) + "\n---\n" + t.slice(sel.end),
      start: sel.start + 5, end: sel.start + 5,
    })),
    h("span", { class: "md-sep" }),
    tool("$", "Math (inline $..$, block $$ on its own line)", (t, sel) => wrap(t, sel, "$")),
    tool("[^]", "Footnote", (t, sel) => footnote(t, sel)),
    tool(":::", "Fold (:::spoiler), or :::info / :::warning / :::danger / :::success",
         (t, sel) => spoiler(t, sel)),
    tool("[TOC]", "Table of contents from the headings", (t, sel) => ({
      text: t.slice(0, sel.start) + "\n[TOC]\n" + t.slice(sel.end),
      start: sel.start + 7, end: sel.start + 7,
    })),
    options.images && h("span", { class: "md-sep" }),
    options.images && pickImage,
    h("span", { class: "spacer" }),
    // One group, so when the toolbar wraps the three modes wrap together.
    h("span", { class: "md-modes" },
      modeBtn("write", "Write"),
      modeBtn("split", "Split"),
      modeBtn("preview", "Preview")));

  const state = h("span", { class: "editor-state" });
  const remove = h(
    "button",
    { class: "linkish danger", type: "button", hidden: isComposer || options.kind !== "note" },
    "Delete",
  );
  const path = h("code", { class: "editor-path" });
  /*
   * Autosave alone is invisible: with no control anywhere, the honest reading
   * of this box is that there is no way to save at all. The button does not add
   * a new way to save -- it commits the same pending edit the timer would -- it
   * is there so the answer to "how do I save this?" is on screen.
   */
  const saveNow = h("button", { class: "btn tiny save", type: "button", hidden: true },
                    "Save now");
  saveNow.addEventListener("click", () => void commit());
  const bar = h("div", { class: "editor-bar" },
    state, h("span", { class: "spacer" }), path, saveNow, remove);
  const root = h("div",
    { class: `editor${options.markdown ? " is-markdown" : ""}`, data: { mode: "write" } },
    titleBox, tools,
    h("div", { class: "md-body" }, area, preview, side),
    bar, picker) as Carrier;

  const dirtyNow = (): boolean =>
    area.value !== saved || (!!options.withTitle && titleBox.value !== savedTitle);

  function paint(): void {
    const blank = isComposer || (!saved && !savedTitle);
    state.textContent = {
      clean: blank ? "" : "Saved",
      dirty: "Unsaved — ⌘↩ or Save now",
      saving: "Saving…",
      saved: "Saved",
      failed: "Could not save",
    }[status];
    state.className = `editor-state ${status}`;
    saveNow.hidden = !(status === "dirty" || status === "failed");
    saveNow.textContent = status === "failed" ? "Try again" : "Save now";
    root.classList.toggle("is-dirty", status === "dirty" || status === "saving");
    // Nothing writes on a timer any more, so the page has to know what is at risk.
    if (status === "dirty" || status === "failed") dirtyEditors.add(options.key);
    else dirtyEditors.delete(options.key);
  }

  async function commit(): Promise<void> {
    const text = area.value;
    const title = titleBox.value;
    if (inFlight) return;
    if (!dirtyNow()) {
      status = "clean";
      paint();
      return;
    }
    // A composer with only a title and no body is still worth keeping.
    if (isComposer && !text.trim() && !title.trim()) return;

    inFlight = true;
    status = "saving";
    paint();
    try {
      const res = options.save
        ? { ...(await options.save(text)), ref, title }
        : await api.saveEntry({
            kind: options.kind === "file" ? "note" : options.kind,
            cwd: options.cwd,
            id: ref,
            host: options.host,
            text,
            ...(options.withTitle ? { title } : {}),
          });
      saved = text;
      savedTitle = res.title ?? title;
      status = "saved";
      if (res.warn) {
        state.textContent = res.warn;
        status = "failed";
      }
      if (res.path) path.textContent = res.path;
      if (isComposer) {
        area.value = "";
        titleBox.value = "";
        saved = "";
        savedTitle = "";
        status = "clean";
        // The composer is not the note it just wrote -- it is the empty box for
        // the next one, so it must not go on showing that note's file path.
        path.textContent = "";
        options.onCreated?.(res.ref);
      } else {
        ref = res.ref;
      }
      options.onSaved?.();
    } catch (err) {
      status = "failed";
      state.textContent = err instanceof Error ? err.message : "Could not save";
    } finally {
      inFlight = false;
      paint();
    }
  }

  const onInput = (): void => {
    status = dirtyNow() ? "dirty" : "clean";
    paintPreview();
    paint();
  };
  area.addEventListener("input", onInput);
  titleBox.addEventListener("input", onInput);
  // Enter in the title line moves into the body rather than submitting anything.
  titleBox.addEventListener("keydown", (e) => {
    if (composing(e)) return;
    if (e.key === "Enter") {
      e.preventDefault();
      area.focus();
    }
  });
  area.addEventListener("keydown", (e) => {
    if (composing(e)) return;
    if (options.markdown) {
      const mod = e.metaKey || e.ctrlKey;
      const key = e.key.toLowerCase();
      if (mod && !e.shiftKey && (key === "b" || key === "i" || key === "k")) {
        e.preventDefault();
        apply((t, sel) => key === "b" ? wrap(t, sel, "**")
          : key === "i" ? wrap(t, sel, "*")
          : link(t, sel));
        return;
      }
      if (e.key === "Tab") {
        e.preventDefault();
        apply((t, sel) => indent(t, sel, e.shiftKey));
        return;
      }
      if (e.key === "Enter" && !e.metaKey && !e.ctrlKey && !e.shiftKey
          && area.selectionStart === area.selectionEnd) {
        const next = continueList(area.value, area.selectionStart);
        if (next) {
          e.preventDefault();
          area.value = next.text;
          area.setSelectionRange(next.start, next.end);
          onInput();
          return;
        }
      }
    }
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      void commit();
      if (isComposer) area.focus();
      else area.blur();
    }
    if (e.key === "Escape") {
      area.value = saved;
      titleBox.value = savedTitle;
      status = "clean";
      paint();
      area.blur();
    }
  });

  remove.addEventListener("click", async () => {
    if (!ref) return;
    if (!confirm("Delete this note?")) return;
    try {
      const gone = ref;
      const res = await api.deleteNote({ cwd: options.cwd, id: ref, host: options.host });
      if (res.warn) alert(res.warn);
      root.remove();
      options.onDeleted?.(gone);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Could not delete");
    }
  });

  paint();

  const instance: Editor = {
    el: root,
    sync(savedText: string, incomingTitle?: string) {
      const title = incomingTitle ?? savedTitle;
      if (status === "dirty" || status === "saving") return;
      if (document.activeElement === area || document.activeElement === titleBox) return;
      if (savedText === saved && title === savedTitle) return;
      saved = savedText;
      savedTitle = title;
      area.value = savedText;
      titleBox.value = title;
      status = "clean";
      paintPreview();
      paint();
    },
    focus() {
      (options.withTitle ? titleBox : area).focus();
    },
    replace(text: string) {
      area.value = text;
      area.setSelectionRange(0, 0);
      onInput();
      area.focus();
    },
  };
  root.__editor = instance;
  return instance;
}

export const focusEditor = (key: string): void => {
  const el = document.querySelector<HTMLTextAreaElement>(`[data-editor="${CSS.escape(key)}"] textarea`);
  el?.focus();
};
