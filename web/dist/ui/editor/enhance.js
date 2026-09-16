/**
 * What the preview gets on top of the rendered markdown, once it is in the DOM:
 * coloured code with a Copy button, typeset math, an anchor on every heading,
 * and an outline of the headings for the side of the pane.
 *
 * The renderer (`markdown.ts`) stays pure and testable; everything here needs
 * a document, and two of the steps need a library that arrives late
 * (`core/vendor.ts`), so they run after the paint and fill in as they can.
 */
import { hljs, katex } from "../../core/vendor.js";
import { h } from "../dom.js";
const HEADINGS = "h1[id],h2[id],h3[id],h4[id],h5[id],h6[id]";
export function enhance(preview) {
    /* code: a Copy button on every fence, colour when the library is in */
    const codes = [...preview.querySelectorAll("pre > code")];
    for (const code of codes) {
        const pre = code.parentElement;
        const box = h("div", { class: "code-block" });
        pre.replaceWith(box);
        const copy = h("button", { class: "code-copy", type: "button", title: "Copy this block" }, "Copy");
        copy.addEventListener("click", () => {
            void navigator.clipboard.writeText(code.textContent ?? "").then(() => { copy.textContent = "Copied"; }, () => { copy.textContent = "Could not copy"; });
            window.setTimeout(() => { copy.textContent = "Copy"; }, 1600);
        });
        box.append(copy, pre);
    }
    if (codes.length)
        void hljs().then((lib) => {
            for (const code of codes)
                if (code.isConnected)
                    lib.highlightElement(code);
        }).catch(() => { });
    /* math: the span keeps its source in data-math until KaTeX replaces it */
    const maths = [...preview.querySelectorAll(".math[data-math]")];
    if (maths.length)
        void katex().then((lib) => {
            for (const el of maths) {
                if (!el.isConnected)
                    continue;
                lib.render(el.dataset.math ?? "", el, { displayMode: el.classList.contains("math-block"), throwOnError: false });
                el.classList.add("is-typeset");
            }
        }).catch(() => { });
    /* headings: an anchor to hover, and the outline for the side */
    const headings = [];
    for (const hd of preview.querySelectorAll(HEADINGS)) {
        headings.push({ level: Number(hd.tagName[1]), id: hd.id, text: hd.textContent ?? "" });
        hd.append(h("a", { class: "anchor", href: `#${hd.id}`, title: "Link to this heading" }, "#"));
    }
    /*
     * Every in-page link -- the anchors, the outline, [TOC], footnotes -- scrolls
     * within this preview rather than the document, so two notes with a "Plan"
     * heading do not fight over the hash, and the app's window never navigates.
     */
    preview.addEventListener("click", (e) => {
        const a = e.target.closest('a[href^="#"]');
        if (!a)
            return;
        const id = decodeURIComponent(a.getAttribute("href")?.slice(1) ?? "");
        const target = id && preview.querySelector(`#${CSS.escape(id)}`);
        if (!target)
            return;
        e.preventDefault();
        target.scrollIntoView({ block: "start", behavior: "smooth" });
    });
    return headings;
}
/** The side outline: one line per heading, indented by level. */
export function outline(headings, jump) {
    const nav = h("nav", { class: "md-outline", hidden: headings.length < 2 });
    for (const hd of headings) {
        const a = h("a", { href: `#${hd.id}`, class: `outline-h${hd.level}` }, hd.text);
        a.addEventListener("click", (e) => {
            e.preventDefault();
            jump(hd.id);
        });
        nav.append(a);
    }
    return nav;
}
//# sourceMappingURL=enhance.js.map