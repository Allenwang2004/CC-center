/*
 * The renderer. Two things matter here: that what you write comes out looking
 * like what you meant, and that nothing you paste can put markup into the page.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { escapeHtml, markdownToHtml } from "../dist/ui/editor/markdown.js";

test("headings, emphasis and code spans", () => {
  assert.equal(markdownToHtml("## Title"), "<h2>Title</h2>");
  assert.equal(markdownToHtml("**bold** and *thin*"),
               "<p><strong>bold</strong> and <em>thin</em></p>");
  assert.equal(markdownToHtml("~~gone~~"), "<p><del>gone</del></p>");
  assert.equal(markdownToHtml("`a * b`"), "<p><code>a * b</code></p>");
});

test("snake_case and a*b survive; emphasis needs a word beside the marker", () => {
  assert.ok(markdownToHtml("read some_long_name here").includes("some_long_name"));
  assert.ok(!markdownToHtml("2 * 3 * 4").includes("<em>"));
});

test("a fenced block is taken whole, markup and all", () => {
  const html = markdownToHtml("```py\nif a < b:\n  **not bold**\n```");
  assert.ok(html.includes('<pre><code class="language-py">'));
  assert.ok(html.includes("a &lt; b"));
  assert.ok(html.includes("**not bold**"));
});

test("a nested list stays inside the item it belongs to", () => {
  const html = markdownToHtml("- one\n  - inner\n- two");
  assert.ok(html.includes("<li>one<ul>") || html.includes("<li>one\n<ul>"),
            `sublist escaped its item: ${html}`);
  assert.equal((html.match(/<ul>/g) || []).length, 2);
  assert.equal((html.match(/<\/ul>/g) || []).length, 2);
});

test("task lists render as checkboxes", () => {
  const html = markdownToHtml("- [x] done\n- [ ] not yet");
  assert.ok(html.includes('<input type="checkbox" disabled checked>'));
  assert.ok(html.includes('<input type="checkbox" disabled>'));
});

test("tables keep their alignment", () => {
  const html = markdownToHtml("| a | b |\n| :-- | --: |\n| 1 | 2 |");
  assert.ok(html.includes('<th style="text-align:left">a</th>'));
  assert.ok(html.includes('<td style="text-align:right">2</td>'));
});

test("blockquotes and rules", () => {
  assert.ok(markdownToHtml("> said\n> so").startsWith("<blockquote>"));
  assert.equal(markdownToHtml("---"), "<hr>");
});

test("links open safely, and only to somewhere safe", () => {
  assert.ok(markdownToHtml("[docs](https://example.com)")
    .includes('rel="noreferrer noopener"'));
  const bad = markdownToHtml("[x](javascript:alert(1))");
  assert.ok(!bad.includes("<a "), bad);
  assert.ok(!markdownToHtml("![x](data:text/html,hi)").includes("<img"));
});

test("anything pasted in is text, never markup", () => {
  assert.equal(escapeHtml('<script>"x"</script>'),
               "&lt;script&gt;&quot;x&quot;&lt;/script&gt;");
  assert.ok(!markdownToHtml("<img src=x onerror=alert(1)>").includes("<img"));
});
