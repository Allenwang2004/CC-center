/**
 * The two libraries the preview borrows, loaded on first use.
 *
 * They live in web/vendor/ as single files built by `npm run vendor` (see
 * web/vendor.mjs), so the page still makes no outside request. The import is
 * computed rather than written as a literal so nothing is fetched until a
 * preview actually holds a code block or a formula -- most never do -- and so
 * the same path works from the server (/static/vendor/) and from inside the
 * app (static/vendor/), both of which sit beside dist/.
 */
const base = new URL("../../vendor/", import.meta.url).href;
let hljsLoading = null;
let katexLoading = null;
let excalidrawLoading = null;
/**
 * Excalidraw is a directory rather than a file: its fonts are fetched by URL
 * at runtime (which is what EXCALIDRAW_ASSET_PATH is for -- without it the
 * library would go to unpkg), and its stylesheet has to be in the page before
 * the first canvas paints.
 */
export const excalidraw = () => (excalidrawLoading ??= (async () => {
    window.EXCALIDRAW_ASSET_PATH = base + "excalidraw/";
    if (!document.querySelector('link[data-vendor="excalidraw"]')) {
        const link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = base + "excalidraw/index.css";
        link.dataset.vendor = "excalidraw";
        const ready = new Promise((resolve) => {
            link.onload = () => resolve();
            link.onerror = () => resolve();
            window.setTimeout(resolve, 3000); // a stylesheet that never answers must not block the canvas
        });
        document.head.append(link);
        await ready;
    }
    return (await import(base + "excalidraw/index.js"));
})());
export const hljs = () => (hljsLoading ??= import(base + "hljs.js").then((m) => m.default));
export const katex = () => (katexLoading ??= import(base + "katex.js").then((m) => m.default));
//# sourceMappingURL=vendor.js.map