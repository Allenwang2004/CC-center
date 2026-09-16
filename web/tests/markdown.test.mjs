/*
 * The renderer. Two things matter here: that what you write comes out looking
 * like what you meant, and that nothing you paste can put markup into the page.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { escapeHtml, markdownToHtml } from "../dist/ui/editor/markdown.js";

test("headings, emphasis and code spans", () => {
  assert.equal(markdownToHtml("## Title"), '<h2 id="title">Title</h2>');
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

test("the HackMD marks: highlight, underline, sup and sub", () => {
  assert.equal(markdownToHtml("==hot== ++under++ x^2^ H~2~O"),
               "<p><mark>hot</mark> <ins>under</ins> x<sup>2</sup> H<sub>2</sub>O</p>");
  // A lone ~ with spaces around it is not a subscript, and ~~ is still a strike.
  assert.ok(!markdownToHtml("2 ~ 3 ~ 4").includes("<sub>"));
  assert.ok(markdownToHtml("~~gone~~").includes("<del>"));
});

test("math is carried as source for KaTeX, and left alone by emphasis", () => {
  const html = markdownToHtml("so $a*b*c$ and $$\\sum_i x_i$$");
  assert.ok(html.includes('<span class="math" data-math="a*b*c">a*b*c</span>'), html);
  assert.ok(!html.includes("<em>"), html);
  const block = markdownToHtml("$$\nE = mc^2\n$$");
  assert.ok(block.includes('class="math math-block" data-math="E = mc^2"'), block);
  // Money is not math.
  assert.ok(!markdownToHtml("costs $5 and $10 today").includes("math"));
});

test("footnotes number by first use and list themselves at the end", () => {
  const html = markdownToHtml("see[^b] and[^a]\n\n[^a]: first defined\n[^b]: used first");
  assert.ok(html.includes('<sup class="fn-ref"><a href="#fn-b" id="fnref-b">1</a></sup>'), html);
  assert.ok(html.includes('<a href="#fn-a" id="fnref-a">2</a>'), html);
  const list = html.slice(html.indexOf('<section class="footnotes">'));
  assert.ok(list.indexOf('id="fn-b"') < list.indexOf('id="fn-a"'), list);
  assert.ok(list.includes("used first"));
});

test("::: makes folds and boxes, and they nest", () => {
  const fold = markdownToHtml(":::spoiler Click me\nhidden **text**\n:::");
  assert.ok(fold.startsWith('<details class="spoiler"><summary>Click me</summary>'), fold);
  assert.ok(fold.includes("<strong>text</strong>"));
  const box = markdownToHtml(":::warning\ncareful\n:::info\ninner\n:::\n:::");
  assert.ok(box.startsWith('<div class="box box-warning">'), box);
  assert.ok(box.includes('<div class="box box-info"><p>inner</p></div>'), box);
  assert.ok(box.endsWith("</div>"), box);
});

test("headings carry ids and [TOC] links to them", () => {
  const html = markdownToHtml("[TOC]\n\n# One two\n\n## One two\n\n### 中文 標題");
  assert.ok(html.includes('<h1 id="one-two">'), html);
  assert.ok(html.includes('<h2 id="one-two-2">'), html);
  assert.ok(html.includes('<h3 id="中文-標題">'), html);
  assert.ok(html.startsWith('<nav class="toc"><ul><li class="toc-h1"><a href="#one-two">One two</a></li>'), html);
  assert.ok(html.includes('<li class="toc-h3"><a href="#中文-標題">'), html);
});
