/*
 * The text surgery behind the toolbar. These are the behaviours that are easy to
 * get subtly wrong and impossible to notice by eye: a wrap that will not come
 * back off, a list that keeps laying down dead bullets, a renumbered list that
 * counts from the wrong place.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  codeBlock, continueList, footnote, heading, indent, link, prefixLines, spoiler, table,
  wrap,
} from "../dist/ui/editor/syntax.js";

const sel = (start, end = start) => ({ start, end });

test("wrap adds markers around the selection and keeps it selected", () => {
  const e = wrap("make this bold", sel(5, 9), "**");
  assert.equal(e.text, "make **this** bold");
  assert.equal(e.text.slice(e.start, e.end), "this");
});

test("wrap toggles back off, whether the markers are inside or outside", () => {
  assert.equal(wrap("a **b** c", sel(2, 7), "**").text, "a b c");
  assert.equal(wrap("a **b** c", sel(4, 5), "**").text, "a b c");
});

test("wrap with nothing selected leaves the caret between the markers", () => {
  const e = wrap("", sel(0), "**");
  assert.equal(e.text, "****");
  assert.equal(e.start, 2);
  assert.equal(e.end, 2);
});

test("prefixLines marks every line it touches, and unmarks when all are marked", () => {
  const on = prefixLines("one\ntwo", sel(0, 7), "> ");
  assert.equal(on.text, "> one\n> two");
  assert.equal(prefixLines(on.text, sel(0, on.text.length), "> ").text, "one\ntwo");
});

test("prefixLines renumbers an ordered list rather than repeating a marker", () => {
  assert.equal(prefixLines("a\nb\nc", sel(0, 5), "", true).text, "1. a\n2. b\n3. c");
});

test("prefixLines leaves blank lines alone", () => {
  assert.equal(prefixLines("a\n\nb", sel(0, 4), "- ").text, "- a\n\n- b");
});

test("heading swaps the level instead of stacking hashes", () => {
  assert.equal(heading("# Title", sel(0), 2).text, "## Title");
  assert.equal(heading("## Title", sel(0), 2).text, "Title");
});

test("link keeps the selection as the label and selects the url placeholder", () => {
  const e = link("see the docs", sel(4, 12));
  assert.equal(e.text, "see [the docs](url)");
  assert.equal(e.text.slice(e.start, e.end), "url");
});

test("code block and table open on their own line", () => {
  assert.equal(codeBlock("x", sel(1)).text, "x\n```\n\n```\n");
  assert.ok(table("", sel(0)).text.startsWith("| Column | Column |\n| --- | --- |"));
});

test("Enter carries a bullet, a number, a task box or a quote to the next line", () => {
  const at = (s) => continueList(s, s.length);
  assert.equal(at("- one").text, "- one\n- ");
  assert.equal(at("3. three").text, "3. three\n4. ");
  assert.equal(at("- [x] done").text, "- [x] done\n- [ ] ");
  assert.equal(at("> quoted").text, "> quoted\n> ");
  assert.equal(at("  - nested").text, "  - nested\n  - ");
});

test("Enter on an empty item ends the list instead of adding another", () => {
  const e = continueList("- one\n- ", 8);
  assert.equal(e.text, "- one\n");
  assert.equal(e.start, 6);
});

test("Enter in ordinary prose is not the editor's business", () => {
  assert.equal(continueList("just a sentence", 15), null);
});

test("indent moves list items by two spaces, and back", () => {
  const inn = indent("- a\n- b", sel(0, 7));
  assert.equal(inn.text, "  - a\n  - b");
  assert.equal(indent(inn.text, sel(0, inn.text.length), true).text, "- a\n- b");
});

test("footnote drops the mark at the caret and the definition at the end", () => {
  const e = footnote("a[^1] b", sel(7));
  assert.equal(e.text, "a[^1] b[^2]\n\n[^2]: ");
  assert.equal(e.start, e.text.length);
});

test("spoiler wraps the selection and selects the title placeholder", () => {
  const e = spoiler("secret", sel(0, 6));
  assert.equal(e.text, ":::spoiler Title\nsecret\n:::\n");
  assert.equal(e.text.slice(e.start, e.end), "Title");
});
