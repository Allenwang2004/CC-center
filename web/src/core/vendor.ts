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

export interface Hljs {
  highlightElement(el: HTMLElement): void;
  getLanguage(name: string): unknown;
}

export interface Katex {
  render(tex: string, el: HTMLElement, options?: { displayMode?: boolean; throwOnError?: boolean }): void;
}

const base = new URL("../../vendor/", import.meta.url).href;

let hljsLoading: Promise<Hljs> | null = null;
let katexLoading: Promise<Katex> | null = null;

export const hljs = (): Promise<Hljs> =>
  (hljsLoading ??= import(base + "hljs.js").then((m) => m.default as Hljs));

export const katex = (): Promise<Katex> =>
  (katexLoading ??= import(base + "katex.js").then((m) => m.default as Katex));
