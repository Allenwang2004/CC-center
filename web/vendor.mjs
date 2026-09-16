/**
 * Third-party code the preview needs, built into web/vendor/ and committed.
 *
 * The page makes no outside requests and ships no bundler, so anything it
 * borrows has to be a plain file it can import from disk: highlight.js for
 * code, KaTeX for math (with its fonts), and Excalidraw for the mind map
 * (with React inside it, and its fonts beside it). Each is bundled once here;
 * the result is committed like web/dist, so the tool still runs with nothing
 * installed. Re-run after bumping a version:
 *
 *   npm run vendor
 */
import { build } from "esbuild";
import { copyFileSync, cpSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const OUT = join(dirname(fileURLToPath(import.meta.url)), "vendor");

rmSync(OUT, { recursive: true, force: true });
mkdirSync(join(OUT, "fonts"), { recursive: true });

const one = async (entry, file) => {
  await build({
    entryPoints: [entry], bundle: true, format: "esm", minify: true, legalComments: "none",
    outfile: join(OUT, file), logLevel: "error",
  });
  console.log(`${file} <- ${entry}`);
};

// Common languages only: the whole set is ten times the size.
await one(join(dirname(require.resolve("highlight.js/package.json")), "es", "common.js"), "hljs.js");
await one(require.resolve("katex/dist/katex.mjs"), "katex.js");

const katex = dirname(require.resolve("katex/dist/katex.mjs"));
copyFileSync(join(katex, "katex.min.css"), join(OUT, "katex.css"));
// woff2 only: every browser this runs in takes it, and the css lists it first.
for (const f of readdirSync(join(katex, "fonts")).filter((f) => f.endsWith(".woff2")))
  copyFileSync(join(katex, "fonts", f), join(OUT, "fonts", f));

/*
 * Excalidraw keeps its locales as lazy chunks and finds its fonts by URL at
 * runtime (window.EXCALIDRAW_ASSET_PATH, set by core/vendor.ts), so it is
 * built with splitting into a directory of its own rather than one file.
 */
const excalidraw = join(dirname(fileURLToPath(import.meta.url)), "..", "node_modules", "@excalidraw", "excalidraw");
await build({
  entryPoints: [join(dirname(fileURLToPath(import.meta.url)), "vendor-src", "excalidraw.js")],
  bundle: true, splitting: true, format: "esm", minify: true, legalComments: "none",
  outdir: join(OUT, "excalidraw"), entryNames: "index", chunkNames: "chunks/[name]-[hash]",
  define: { "process.env.NODE_ENV": '"production"', "process.env.IS_PREACT": '"false"' },
  logLevel: "error",
});
copyFileSync(join(excalidraw, "dist", "prod", "index.css"), join(OUT, "excalidraw", "index.css"));
cpSync(join(excalidraw, "dist", "prod", "fonts"), join(OUT, "excalidraw", "fonts"), { recursive: true });
console.log("excalidraw/ <- @excalidraw/excalidraw (bundle, css, fonts)");

const versions = ["highlight.js", "katex", "react"]
  .map((n) => `${n} ${require(`${n}/package.json`).version}`)
  .concat(`@excalidraw/excalidraw ${JSON.parse(readFileSync(join(excalidraw, "package.json"), "utf8")).version}`);
writeFileSync(join(OUT, "VERSIONS"), versions.join("\n") + "\n");
console.log(versions.join(", "));
