/**
 * The computed half of the record, shown under Sessions.
 *
 * Two things the tool worked out and you did not write: what a day's work
 * changed in the project, and what each single question actually did. They live
 * together because they answer the same question at two zoom levels -- the day,
 * and the turn.
 */
import { h, sticky } from "../ui/dom.js";
import { clock, count, plural, relPath, shortCommand } from "../core/format.js";
import { store } from "../core/store.js";
/* -- a day in one project ------------------------------------------------ */
export function changeSummary(ch) {
    const lines = [];
    if (ch.commits.length) {
        lines.push(h("div", { class: "fact" }, h("span", { class: "fact-key" }, "landed"), h("span", { class: "fact-value" }, plural(ch.commits.length, "commit"), h("span", { class: "muted" }, plural(ch.commit_files, "file")), h("span", { class: "plus" }, `+${count(ch.commit_added)}`), h("span", { class: "minus" }, `−${count(ch.commit_removed)}`))));
        for (const c of ch.commits)
            lines.push(h("div", { class: "fact sub" }, h("span", { class: "fact-key" }, clock(c.at)), h("span", { class: "fact-value" }, h("button", { class: "sha", type: "button", title: c.sha, data: { copy: c.sha } }, c.short), h("span", { class: "commit-subject" }, c.subject), h("span", { class: "muted" }, plural(c.files.length, "file")), h("span", { class: "plus" }, `+${c.added}`), h("span", { class: "minus" }, `−${c.removed}`))));
    }
    else if (ch.has_git) {
        lines.push(h("div", { class: "fact" }, h("span", { class: "fact-key" }, "landed"), h("span", { class: "fact-value muted" }, "Nothing was committed")));
    }
    if (ch.uncommitted.length) {
        const dirty = ch.uncommitted.filter((u) => u.still_dirty).length;
        lines.push(h("div", { class: "fact open" }, h("span", { class: "fact-key" }, "open"), h("span", { class: "fact-value" }, `${plural(ch.uncommitted.length, "file")} never reached a commit`, h("span", { class: "muted" }, !ch.has_git ? "not a git repo"
            : dirty ? `${dirty} still dirty in the working tree`
                : "all cleaned up since"))));
        for (const u of ch.uncommitted.slice(0, 8))
            lines.push(h("div", { class: "fact sub" }, h("span", { class: "fact-key" }, ""), h("span", { class: "fact-value" }, h("code", { class: "token" }, u.path), h("span", { class: "plus" }, `+${u.a}`), h("span", { class: "minus" }, `−${u.d}`), h("span", { class: "muted" }, `from #${u.turns.slice(0, 3).join(", #")}`, ch.has_git && !u.still_dirty ? " · clean now" : ""))));
        if (ch.uncommitted.length > 8)
            lines.push(h("div", { class: "fact sub" }, h("span", { class: "fact-key" }, ""), h("span", { class: "fact-value muted" }, `and ${ch.uncommitted.length - 8} more`)));
    }
    const st = ch.turn_stats;
    const spread = [
        st.with_commit && `${st.with_commit} reached a commit`,
        st.touched_only && `${st.touched_only} changed things but stopped short`,
        st.looked_only && `${st.looked_only} only looked around`,
    ].filter(Boolean);
    if (spread.length)
        lines.push(h("div", { class: "fact" }, h("span", { class: "fact-key" }, "of these"), h("span", { class: "fact-value muted" }, spread.join(" · "))));
    return h("section", { class: "summary" }, h("h4", null, "What changed", h("span", { class: "muted" }, "read from commit stats and the transcript")), lines);
}
/* -- what a turn ran ----------------------------------------------------- */
/** Commands past this stay behind a "+N more", so a long turn still scans. */
const CMD_ROWS = 6;
/**
 * One command, click for all of it.
 *
 * The row shows as much as the column fits; opening it gives the whole line --
 * the flags, the paths, the body of the inline script -- because "ran python3"
 * is not a record of anything, and this list is most of what an agent did.
 * A command's newlines are already collapsed to spaces upstream, in the
 * scanner, so what opens is one long line wrapped, not the script as typed.
 */
function commandLine(cmd, key) {
    return sticky(store.openDays, key, { class: "cmd" }, 
    // No tooltip: hovering a truncated command was the old answer, and it is
    // what sent you here. The row is the glance, opening it is the record.
    h("summary", null, h("code", null, shortCommand(cmd, 160))), h("div", { class: "cmd-full" }, h("pre", null, cmd), h("button", { class: "btn ghost tiny", type: "button", data: { copy: cmd } }, "Copy")));
}
/**
 * A question with everything the agent did before you spoke again. The rule
 * down the left is the record column and the clock sits outside it, in the
 * margin, so a session can be scanned by time alone.
 */
export function turnRow(row, index, cwd) {
    const { turn } = row;
    const files = Object.entries(turn.files)
        .sort((a, b) => b[1].a + b[1].d - (a[1].a + a[1].d));
    const key = `cmd:${row.session.session_id}:${index}`;
    const detail = [];
    if (files.length)
        detail.push(h("div", { class: "detail" }, h("span", { class: "detail-key" }, "edited"), h("span", { class: "detail-value" }, files.slice(0, 6).map(([file, st]) => h("span", { class: "token", title: file }, relPath(file, cwd), (st.a || st.d) &&
            h("span", { class: "delta" }, h("span", { class: "plus" }, `+${st.a}`), h("span", { class: "minus" }, `−${st.d}`)))), files.length > 6 && h("span", { class: "muted" }, `+${files.length - 6} more`))));
    if (turn.commands.length) {
        const lines = turn.commands.map((c, i) => commandLine(c, `${key}:${i}`));
        detail.push(h("div", { class: "detail" }, h("span", { class: "detail-key" }, "ran"), h("div", { class: "detail-value cmds" }, lines.slice(0, CMD_ROWS), lines.length > CMD_ROWS
            ? sticky(store.openDays, `${key}:rest`, { class: "cmd more" }, h("summary", null, `${lines.length - CMD_ROWS} more`), lines.slice(CMD_ROWS))
            : null)));
    }
    for (const c of turn.commits)
        detail.push(h("div", { class: "detail landed" }, h("span", { class: "detail-key" }, "commit"), h("span", { class: "detail-value" }, h("button", { class: "sha", type: "button", title: `${c.sha} — click to copy`,
            data: { copy: c.sha } }, c.short), h("span", { class: "commit-subject" }, c.subject), h("span", { class: "delta" }, h("span", { class: "muted" }, plural(c.files.length, "file")), h("span", { class: "plus" }, `+${c.added}`), h("span", { class: "minus" }, `−${c.removed}`)))));
    if (!detail.length)
        detail.push(h("div", { class: "detail" }, h("span", { class: "detail-key" }, ""), h("span", { class: "detail-value muted" }, "Nothing was written")));
    return h("article", { class: `entry${turn.commits.length ? " landed" : ""}` }, h("time", { class: "margin-clock" }, clock(turn.ts)), h("div", { class: "entry-body" }, h("p", { class: "asked" }, turn.kind === "slash" && h("span", { class: "tag" }, "command"), turn.text), detail, h("div", { class: "detail" }, h("span", { class: "detail-key" }, ""), h("span", { class: "detail-value muted" }, `#${index}`))));
}
//# sourceMappingURL=detail.js.map