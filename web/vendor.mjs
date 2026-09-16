/**
 * Third-party code the preview needs, built into web/vendor/ and committed.
 *
 * The page makes no outside requests and ships no bundler, so anything it
 * borrows has to be a plain file it can import from disk: highlight.js for
 * code, KaTeX for math (with its fonts). Each is bundled once into a single ES
 * module here; the result is committed like web/dist, so the tool still runs
 * with nothing installed. Re-run after bumping a version:
 *
 *   npm run vendor
 */
import { build } from "esbuild";
import { copyFileSync, mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
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

const versions = ["highlight.js", "katex"].map((n) => `${n} ${require(`${n}/package.json`).version}`);
writeFileSync(join(OUT, "VERSIONS"), versions.join("\n") + "\n");
console.log(versions.join(", "));
