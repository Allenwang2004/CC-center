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

/* -- Excalidraw ---------------------------------------------------------- */

/** An element of the scene. Excalidraw owns the shape; the page only carries it. */
export type SceneElement = Record<string, unknown> & { id: string; isDeleted?: boolean };
export type SceneFiles = Record<string, { id: string; mimeType: string; dataURL: string }>;

/** The `.excalidraw` file format, which is what a mind map is stored as. */
export interface SceneJSON {
  type: "excalidraw";
  elements: SceneElement[];
  appState?: Record<string, unknown>;
  files?: SceneFiles;
}

/** The imperative handle Excalidraw hands back through `excalidrawAPI`. */
export interface ExcalidrawAPI {
  getSceneElements(): readonly SceneElement[];
  getAppState(): Record<string, unknown>;
  getFiles(): SceneFiles;
  updateScene(scene: { elements?: readonly SceneElement[]; appState?: Record<string, unknown> }): void;
  addFiles(files: SceneFiles[string][]): void;
  scrollToContent(target?: unknown, opts?: { fitToContent?: boolean; animate?: boolean }): void;
}

export interface ExcalidrawProps {
  initialData?: { elements?: SceneElement[]; appState?: Record<string, unknown>; files?: SceneFiles } | null;
  theme?: "light" | "dark";
  langCode?: string;
  name?: string;
  autoFocus?: boolean;
  excalidrawAPI?: (api: ExcalidrawAPI) => void;
  onChange?: (elements: readonly SceneElement[], appState: Record<string, unknown>, files: SceneFiles) => void;
}

export interface Excalidraw {
  mount(el: HTMLElement, props: ExcalidrawProps): { update(props: ExcalidrawProps): void; destroy(): void };
  serializeAsJSON(elements: readonly SceneElement[], appState: Record<string, unknown>,
                  files: SceneFiles, type: "local" | "database"): string;
  getSceneVersion(elements: readonly SceneElement[]): number;
  restore(data: Partial<SceneJSON> | null, localAppState: unknown, localElements: unknown,
          opts?: { repairBindings?: boolean }):
    { elements: SceneElement[]; appState: Record<string, unknown>; files: SceneFiles };
}

declare global {
  interface Window {
    EXCALIDRAW_ASSET_PATH?: string;
  }
}

const base = new URL("../../vendor/", import.meta.url).href;

let hljsLoading: Promise<Hljs> | null = null;
let katexLoading: Promise<Katex> | null = null;
let excalidrawLoading: Promise<Excalidraw> | null = null;

/**
 * Excalidraw is a directory rather than a file: its fonts are fetched by URL
 * at runtime (which is what EXCALIDRAW_ASSET_PATH is for -- without it the
 * library would go to unpkg), and its stylesheet has to be in the page before
 * the first canvas paints.
 */
export const excalidraw = (): Promise<Excalidraw> =>
  (excalidrawLoading ??= (async () => {
    window.EXCALIDRAW_ASSET_PATH = base + "excalidraw/";
    if (!document.querySelector('link[data-vendor="excalidraw"]')) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = base + "excalidraw/index.css";
      link.dataset.vendor = "excalidraw";
      const ready = new Promise<void>((resolve) => {
        link.onload = () => resolve();
        link.onerror = () => resolve();
        window.setTimeout(resolve, 3000);      // a stylesheet that never answers must not block the canvas
      });
      document.head.append(link);
      await ready;
    }
    return (await import(base + "excalidraw/index.js")) as Excalidraw;
  })());

export const hljs = (): Promise<Hljs> =>
  (hljsLoading ??= import(base + "hljs.js").then((m) => m.default as Hljs));

export const katex = (): Promise<Katex> =>
  (katexLoading ??= import(base + "katex.js").then((m) => m.default as Katex));
