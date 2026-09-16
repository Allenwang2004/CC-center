/**
 * The text surgery behind the markdown toolbar and its keyboard shortcuts.
 *
 * These are pure functions over (text, selection) so the behaviour that is easy
 * to get subtly wrong -- toggling a wrap back off, prefixing exactly the lines
 * you touched, continuing a list on Enter -- can be tested without a DOM.
 */
const lineStartOf = (text, at) => text.lastIndexOf("\n", at - 1) + 1;
const lineEndOf = (text, at) => {
    const i = text.indexOf("\n", at);
    return i === -1 ? text.length : i;
};
/**
 * Toggle `before…after` around the selection. With nothing selected it drops in
 * the markers and puts the caret between them, so Cmd+B then typing works.
 */
export function wrap(text, sel, before, after = before) {
    const { start, end } = sel;
    const inner = text.slice(start, end);
    // Already wrapped, either inside the selection or just outside it -- take it off.
    if (inner.startsWith(before) && inner.endsWith(after)
        && inner.length >= before.length + after.length) {
        const bare = inner.slice(before.length, inner.length - after.length);
        return { text: text.slice(0, start) + bare + text.slice(end),
            start, end: start + bare.length };
    }
    const outerStart = start - before.length;
    if (outerStart >= 0
        && text.slice(outerStart, start) === before
        && text.slice(end, end + after.length) === after) {
        return { text: text.slice(0, outerStart) + inner + text.slice(end + after.length),
            start: outerStart, end: outerStart + inner.length };
    }
    return { text: text.slice(0, start) + before + inner + after + text.slice(end),
        start: start + before.length, end: start + before.length + inner.length };
}
/**
 * Put `prefix` on every line the selection touches, or take it off if every one
 * of them already has it. `ordered` renumbers instead of repeating the marker.
 */
export function prefixLines(text, sel, prefix, ordered = false) {
    const from = lineStartOf(text, sel.start);
    const to = lineEndOf(text, sel.end);
    const lines = text.slice(from, to).split("\n");
    const has = (l) => ordered ? /^\s*\d+[.)]\s/.test(l) : l.startsWith(prefix);
    const strip = (l) => ordered ? l.replace(/^\s*\d+[.)]\s/, "") : l.slice(prefix.length);
    const allHave = lines.every((l) => !l.trim() || has(l));
    const next = lines.map((l, i) => {
        if (!l.trim())
            return l;
        if (allHave)
            return strip(l);
        const bare = has(l) ? strip(l) : l;
        return ordered ? `${i + 1}. ${bare}` : prefix + bare;
    });
    const body = next.join("\n");
    return { text: text.slice(0, from) + body + text.slice(to),
        start: from, end: from + body.length };
}
/** Headings toggle between levels rather than stacking `#` forever. */
export function heading(text, sel, level) {
    const from = lineStartOf(text, sel.start);
    const to = lineEndOf(text, sel.end);
    const want = "#".repeat(level) + " ";
    const lines = text.slice(from, to).split("\n").map((l) => {
        const bare = l.replace(/^#{1,6}\s+/, "");
        return l.startsWith(want) ? bare : want + bare;
    });
    const body = lines.join("\n");
    return { text: text.slice(0, from) + body + text.slice(to),
        start: from, end: from + body.length };
}
/** A link keeps whatever you selected as the label. */
export function link(text, sel) {
    const label = text.slice(sel.start, sel.end) || "text";
    const made = `[${label}](url)`;
    const at = sel.start + made.length - 4; // select the "url" placeholder
    return { text: text.slice(0, sel.start) + made + text.slice(sel.end),
        start: at, end: at + 3 };
}
export function codeBlock(text, sel) {
    const inner = text.slice(sel.start, sel.end);
    const atLineStart = sel.start === lineStartOf(text, sel.start);
    const lead = atLineStart ? "" : "\n";
    const made = `${lead}\`\`\`\n${inner}\n\`\`\`\n`;
    const at = sel.start + lead.length + 4;
    return { text: text.slice(0, sel.start) + made + text.slice(sel.end),
        start: at, end: at + inner.length };
}
export function table(text, sel) {
    const atLineStart = sel.start === lineStartOf(text, sel.start);
    const lead = atLineStart ? "" : "\n";
    const made = `${lead}| Column | Column |\n| --- | --- |\n|  |  |\n`;
    const at = sel.start + lead.length + 2;
    return { text: text.slice(0, sel.start) + made + text.slice(sel.end),
        start: at, end: at + 6 };
}
/**
 * A footnote is two edits at once: the `[^n]` mark where the caret is, and the
 * `[^n]: ` line at the end of the text, where the caret then goes. `n` is one
 * past the highest footnote already there.
 */
export function footnote(text, sel) {
    let n = 0;
    for (const m of text.matchAll(/\[\^(\d+)\]/g))
        n = Math.max(n, Number(m[1]));
    const id = String(n + 1);
    const mark = `[^${id}]`;
    const trimmed = text.slice(0, sel.start) + mark + text.slice(sel.end);
    const gap = trimmed.endsWith("\n\n") ? "" : trimmed.endsWith("\n") ? "\n" : "\n\n";
    const made = trimmed + `${gap}[^${id}]: `;
    return { text: made, start: made.length, end: made.length };
}
/** `:::spoiler Title` around the selection; the title is what gets selected. */
export function spoiler(text, sel) {
    const inner = text.slice(sel.start, sel.end);
    const atLineStart = sel.start === lineStartOf(text, sel.start);
    const lead = atLineStart ? "" : "\n";
    const made = `${lead}:::spoiler Title\n${inner}\n:::\n`;
    const at = sel.start + lead.length + ":::spoiler ".length;
    return { text: text.slice(0, sel.start) + made + text.slice(sel.end),
        start: at, end: at + 5 };
}
/**
 * Enter inside a list carries the marker to the next line; Enter on an item you
 * left empty ends the list instead of laying down another dead bullet.
 */
export function continueList(text, caret) {
    const from = lineStartOf(text, caret);
    const line = text.slice(from, caret);
    const task = /^(\s*)([-*+])\s+\[([ xX])\]\s+(.*)$/.exec(line);
    if (task) {
        if (!task[4].trim()) {
            const cut = text.slice(0, from) + text.slice(caret);
            return { text: cut, start: from, end: from };
        }
        const made = `\n${task[1]}${task[2]} [ ] `;
        return { text: text.slice(0, caret) + made + text.slice(caret),
            start: caret + made.length, end: caret + made.length };
    }
    const bullet = /^(\s*)([-*+])\s+(.*)$/.exec(line);
    if (bullet) {
        if (!bullet[3].trim()) {
            const cut = text.slice(0, from) + text.slice(caret);
            return { text: cut, start: from, end: from };
        }
        const made = `\n${bullet[1]}${bullet[2]} `;
        return { text: text.slice(0, caret) + made + text.slice(caret),
            start: caret + made.length, end: caret + made.length };
    }
    const ordered = /^(\s*)(\d+)([.)])\s+(.*)$/.exec(line);
    if (ordered) {
        if (!ordered[4].trim()) {
            const cut = text.slice(0, from) + text.slice(caret);
            return { text: cut, start: from, end: from };
        }
        const made = `\n${ordered[1]}${Number(ordered[2]) + 1}${ordered[3]} `;
        return { text: text.slice(0, caret) + made + text.slice(caret),
            start: caret + made.length, end: caret + made.length };
    }
    const quote = /^(\s*>\s?)(.*)$/.exec(line);
    if (quote) {
        if (!quote[2].trim()) {
            const cut = text.slice(0, from) + text.slice(caret);
            return { text: cut, start: from, end: from };
        }
        const made = `\n${quote[1]}`;
        return { text: text.slice(0, caret) + made + text.slice(caret),
            start: caret + made.length, end: caret + made.length };
    }
    return null;
}
/** Tab and Shift+Tab move list items in and out by two spaces. */
export function indent(text, sel, out = false) {
    const from = lineStartOf(text, sel.start);
    const to = lineEndOf(text, sel.end);
    const lines = text.slice(from, to).split("\n").map((l) => out ? l.replace(/^ {1,2}/, "") : (l.trim() ? "  " + l : l));
    const body = lines.join("\n");
    return { text: text.slice(0, from) + body + text.slice(to),
        start: from, end: from + body.length };
}
//# sourceMappingURL=syntax.js.map