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
export const hljs = () => (hljsLoading ??= import(base + "hljs.js").then((m) => m.default));
export const katex = () => (katexLoading ??= import(base + "katex.js").then((m) => m.default));
//# sourceMappingURL=vendor.js.map