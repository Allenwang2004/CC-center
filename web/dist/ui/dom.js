/**
 * A very small DOM helper. No framework: the data here is small enough that
 * rebuilding a pane is cheap, and staying dependency-free keeps the tool
 * runnable straight from disk.
 *
 * The one thing a full rebuild would break is a textarea you are typing in, so
 * anything editable is built once and kept alive by `keep()`.
 */
function append(parent, child) {
    if (child === null || child === undefined || child === false || child === "")
        return;
    if (Array.isArray(child)) {
        for (const c of child)
            append(parent, c);
        return;
    }
    parent.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
}
/**
 * True while an input method is still composing a character.
 *
 * Typing Chinese, Japanese or Korean, the Enter that accepts the candidate also
 * arrives as a keydown here. Acting on it runs the binding a second time on the
 * same press -- the character is confirmed *and* the editor inserts a newline or
 * carries on the list. Every key binding over a text field has to sit this one
 * out. `keyCode === 229` is the fallback for IMEs that never set isComposing.
 */
export const composing = (e) => e.isComposing || e.keyCode === 229;
export function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    if (attrs) {
        for (const [k, v] of Object.entries(attrs)) {
            if (v === undefined || v === null)
                continue;
            if (k === "data") {
                for (const [dk, dv] of Object.entries(v))
                    if (dv !== undefined)
                        el.dataset[dk] = dv;
            }
            else if (k === "aria") {
                for (const [ak, av] of Object.entries(v))
                    if (av !== undefined)
                        el.setAttribute("aria-" + ak, av);
            }
            else if (k === "on") {
                for (const [ev, fn] of Object.entries(v))
                    el.addEventListener(ev, fn);
            }
            else if (k === "style") {
                Object.assign(el.style, v);
            }
            else if (k === "class") {
                el.className = String(v);
            }
            else if (
            /*
             * Set as properties, never attributes. HTML reads a boolean attribute
             * as true the moment it is present, so `open: false` written out as
             * open="false" leaves a <details> open -- which is how every session
             * card came back expanded no matter what was remembered.
             */
            k === "value" || k === "checked" || k === "disabled" || k === "hidden"
                || k === "open") {
                el[k] = v;
            }
            else {
                el.setAttribute(k, String(v));
            }
        }
    }
    for (const c of children)
        append(el, c);
    return el;
}
/**
 * A <details> that remembers whether it was open.
 *
 * A background scan rebuilds a whole pane, and a rebuilt <details> comes back
 * closed -- which shuts the record you were reading mid-sentence. The caller
 * passes the set it keeps open keys in; the element writes its own key in and
 * out of that set. `open: true` in the attributes still wins, for the panes
 * that start open and are only ever closed by hand.
 */
export function sticky(open, key, attrs, ...children) {
    const box = h("details", { ...attrs, open: attrs?.open || open.has(key) }, ...children);
    box.addEventListener("toggle", () => {
        if (box.open)
            open.add(key);
        else
            open.delete(key);
    });
    return box;
}
/**
 * The same, for a <details> that starts open: the set holds the keys you
 * closed, so a shelf you folded stays folded through a redraw and everything
 * else stays open, without the caller having to seed the set.
 */
export function stickyOpen(closed, key, attrs, ...children) {
    const box = h("details", { ...attrs, open: !closed.has(key) }, ...children);
    box.addEventListener("toggle", () => {
        if (box.open)
            closed.delete(key);
        else
            closed.add(key);
    });
    return box;
}
export const frag = (...children) => {
    const f = document.createDocumentFragment();
    for (const c of children)
        append(f, c);
    return f;
};
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
export function mount(host, ...children) {
    host.replaceChildren();
    append(host, children);
}
/**
 * Long-lived nodes, addressed by key. Editors live here so a background
 * refresh can rebuild the page around them without dropping your caret.
 */
const kept = new Map();
export function keep(key, build) {
    const existing = kept.get(key);
    if (existing)
        return existing;
    const made = build();
    kept.set(key, made);
    return made;
}
export const forget = (key) => void kept.delete(key);
export function forgetPrefix(prefix) {
    for (const k of [...kept.keys()])
        if (k.startsWith(prefix))
            kept.delete(k);
}
/* -- toast ---------------------------------------------------------------- */
let toastTimer;
/** One line at the bottom that goes away by itself. */
export function toast(message) {
    const el = document.getElementById("toast");
    if (!el)
        return;
    el.textContent = message;
    el.classList.add("show");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => el.classList.remove("show"), 2600);
}
//# sourceMappingURL=dom.js.map