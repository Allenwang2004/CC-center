/**
 * The mind map: one Excalidraw canvas per project, at the top of its block.
 *
 * It follows the editor's rules, not a whiteboard's. Nothing is saved on a
 * timer: you draw, the bar says "Unsaved", and Save now (or ⌘↩ / ⌘S on the
 * canvas) writes the scene to the account as one `mindmap` row, the standard
 * `.excalidraw` JSON, so it opens in Excalidraw itself too. The canvas is held
 * by `keep()` like a textarea, so a background refresh redraws the page around
 * it and never resets what you were drawing.
 *
 * Excalidraw is heavy (React and all), so the block starts folded and the
 * canvas mounts the first time you open it. `web/vendor/excalidraw/` is where
 * it comes from; `core/vendor.ts` is how.
 */
import { api } from "../core/api.js";
import { basename, clock, todayKey } from "../core/format.js";
import { store } from "../core/store.js";
import { excalidraw as loadExcalidraw } from "../core/vendor.js";
import { h, keep, sticky } from "./dom.js";
import { setUnsaved } from "./editor/editor.js";
/** A stored scene, or null when the text is empty or not a scene at all. */
function parseScene(text) {
    if (!text.trim())
        return null;
    try {
        const data = JSON.parse(text);
        if (!data || !Array.isArray(data.elements))
            return null;
        return { type: "excalidraw", elements: data.elements,
            appState: data.appState ?? {}, files: data.files ?? {} };
    }
    catch {
        return null;
    }
}
export function board(options) {
    const el = keep(`board:${options.cwd}`, () => build(options).el);
    return el.__board;
}
function build(options) {
    let ref = options.ref;
    let saved = options.saved;
    let updatedAt = options.updatedAt;
    let status = "clean";
    let inFlight = false;
    let lib = null;
    let ex = null;
    let mounted = null;
    /** The scene version the saved text corresponds to; -1 until the canvas is up. */
    let savedVersion = -1;
    let savedLook = "";
    const key = `mindmap:${options.cwd}`;
    const state = h("span", { class: "editor-state" });
    const when = h("span", { class: "muted" });
    const saveNow = h("button", { class: "btn tiny save", type: "button", hidden: true }, "Save now");
    const expand = h("button", { class: "linkish", type: "button", title: "Fill the window" }, "Expand");
    const canvas = h("div", { class: "board-canvas" });
    const loading = h("p", { class: "board-loading muted" }, "Loading the canvas…");
    const root = sticky(store.openBoards, options.cwd, { class: "shelf fold mindmap board" }, h("summary", null, h("h4", null, "Mindmap", when)), h("div", { class: "editor-bar board-bar" }, state, h("span", { class: "spacer" }), saveNow, expand), h("div", { class: "board-host" }, loading, canvas));
    const look = (appState) => `${String(appState.viewBackgroundColor ?? "")}|${String(appState.gridSize ?? "")}`;
    function paint() {
        when.textContent = updatedAt
            ? `saved ${clock(updatedAt)} · ${updatedAt.slice(0, 10)}`
            : "not drawn yet";
        state.textContent = {
            clean: saved ? "Saved" : "",
            dirty: "Unsaved — ⌘↩ or Save now",
            saving: "Saving…",
            saved: "Saved",
            failed: "Could not save",
        }[status];
        state.className = `editor-state ${status}`;
        saveNow.hidden = !(status === "dirty" || status === "failed");
        saveNow.textContent = status === "failed" ? "Try again" : "Save now";
        root.classList.toggle("is-dirty", status === "dirty" || status === "saving");
        setUnsaved(key, status === "dirty" || status === "failed");
    }
    /** Whether what is on the canvas differs from what was last saved. */
    const dirtyNow = () => {
        if (!lib || !ex)
            return false;
        return lib.getSceneVersion(ex.getSceneElements()) !== savedVersion
            || look(ex.getAppState()) !== savedLook;
    };
    async function commit() {
        if (!lib || !ex || inFlight)
            return;
        if (!dirtyNow()) {
            status = "clean";
            paint();
            return;
        }
        const elements = ex.getSceneElements();
        const appState = ex.getAppState();
        const files = ex.getFiles();
        const text = lib.serializeAsJSON(elements, appState, files, "local");
        inFlight = true;
        status = "saving";
        paint();
        try {
            const res = await api.saveEntry({ kind: "mindmap", cwd: options.cwd,
                id: ref || todayKey(), host: options.host, text });
            ref = res.ref;
            saved = text;
            savedVersion = lib.getSceneVersion(elements);
            savedLook = look(appState);
            updatedAt = new Date().toISOString();
            status = "saved";
        }
        catch (err) {
            status = "failed";
            paint();
            state.textContent = err instanceof Error ? err.message : "Could not save";
            inFlight = false;
            return;
        }
        inFlight = false;
        paint();
    }
    /* -- mounting ---------------------------------------------------------- */
    const dark = () => window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    async function mount() {
        if (mounted)
            return;
        try {
            lib = await loadExcalidraw();
        }
        catch (err) {
            loading.textContent = "Could not load the canvas: "
                + (err instanceof Error ? err.message : String(err))
                + ". Run `npm run vendor` and reload.";
            loading.classList.add("failed");
            return;
        }
        if (mounted)
            return;
        const scene = parseScene(saved);
        const initial = scene ? lib.restore(scene, null, null, { repairBindings: true }) : null;
        /*
         * The baseline is the scene as stored, not what the canvas reports when
         * its API arrives: initialData is applied after that, so reading it then
         * would make every saved map open as "Unsaved".
         */
        savedVersion = lib.getSceneVersion(initial?.elements ?? []);
        if (initial)
            savedLook = look(initial.appState);
        mounted = lib.mount(canvas, {
            initialData: initial
                ? { elements: initial.elements, appState: initial.appState, files: initial.files }
                : null,
            theme: dark(),
            langCode: "en",
            name: basename(options.cwd),
            autoFocus: false,
            excalidrawAPI: (handle) => {
                ex = handle;
                if (!initial)
                    savedLook = look(handle.getAppState());
            },
            onChange: () => {
                if (inFlight || !ex)
                    return;
                const next = dirtyNow() ? "dirty" : (status === "saved" ? "saved" : "clean");
                if (next !== status) {
                    status = next;
                    paint();
                }
            },
        });
        loading.remove();
    }
    root.addEventListener("toggle", () => {
        if (root.open)
            void mount();
    });
    if (root.open)
        void mount();
    saveNow.addEventListener("click", () => void commit());
    expand.addEventListener("click", () => {
        const on = root.classList.toggle("is-expanded");
        expand.textContent = on ? "Shrink" : "Expand";
        document.body.classList.toggle("has-board-expanded", on);
        // Excalidraw sizes itself to its container; nudge it after the change.
        window.dispatchEvent(new Event("resize"));
    });
    // Save from the canvas: the same chord as the editors, plus the one everyone tries.
    root.addEventListener("keydown", (e) => {
        if ((e.metaKey || e.ctrlKey) && (e.key === "Enter" || e.key.toLowerCase() === "s")) {
            e.preventDefault();
            e.stopPropagation();
            void commit();
            return;
        }
        /*
         * Escape backs out one level, the way it does inside Excalidraw: a tool
         * or a selection first, and only with nothing left to drop does it shrink
         * the expanded canvas back into the page.
         */
        if (e.key === "Escape" && root.classList.contains("is-expanded") && ex
            && !e.target.matches("input, textarea")) {
            const st = ex.getAppState();
            const idle = !Object.keys(st.selectedElementIds ?? {}).length
                && (st.activeTool?.type ?? "selection") === "selection" && !st.editingTextElement;
            if (idle)
                expand.click();
        }
    }, true);
    paint();
    const instance = {
        el: root,
        sync(savedText, savedRef, savedAt) {
            if (status === "dirty" || status === "saving" || status === "failed")
                return;
            if (savedText === saved && savedRef === ref)
                return;
            saved = savedText;
            ref = savedRef;
            updatedAt = savedAt;
            status = "clean";
            if (lib && ex) {
                const scene = parseScene(savedText);
                const next = lib.restore(scene ?? { elements: [] }, null, null, { repairBindings: true });
                ex.updateScene({ elements: next.elements, appState: next.appState });
                ex.addFiles(Object.values(next.files));
                savedVersion = lib.getSceneVersion(next.elements);
                savedLook = look(next.appState);
            }
            paint();
        },
    };
    root.__board = instance;
    return instance;
}
//# sourceMappingURL=board.js.map