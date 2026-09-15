"""
The by-day report: what the machines did today, one day at a time.

The default `cc-center` output, and what the interface's Report tab shows. Reads
one day's slices across every session and every machine, so the totals at the
top of a day are the totals for that whole day, not for one project.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ..analysis import day_cost, dedupe_commands
from ..scanner import (
    NOISE_TOOLS,
    WEEKDAY,
    human_num,
    human_time,
    local,
    oneline,
    plural,
    rel,
    title_of,
)


def render(sessions, multi_host, prompt_lines=5, show_tokens=False):
    # (day -> (host, cwd) -> [(session, dayslice)])
    buckets = defaultdict(lambda: defaultdict(list))
    for s in sessions:
        for key, day in s["days"].items():
            buckets[key][(s["host"], s["cwd"] or "(unknown)")].append((s, day))

    if not buckets:
        return "No sessions in this range."

    out = []
    for daykey in sorted(buckets, reverse=True):
        groups = buckets[daykey]
        pairs = [p for g in groups.values() for p in g]
        n_sessions = len(pairs)
        hosts = sorted({h for h, _ in groups})

        tot_prompts = sum(len(d["prompts"]) for _, d in pairs)
        tot_files = Counter()
        for _, d in pairs:
            tot_files.update(d["files"])
        tot_add = sum(d["added"] for _, d in pairs)
        tot_del = sum(d["removed"] for _, d in pairs)
        tot_active = sum(d["active"] for _, d in pairs)
        tot_cost = sum(day_cost(s, daykey) for s, _ in pairs)
        # d["commits"] 是從 `git commit -m` 指令刮下來的訊息, 只是「打算 commit」——
        # heredoc 裡引用到的字串也會中, 不是 git repo 也會中。turn 的 commits 是
        # git log 回來對上的, 所以要數就數這個。
        commits = [c for _, d in pairs for t in d["turns"] for c in t["commits"]]
        artifacts, seen_art = [], set()
        for _, d in pairs:
            for a in d.get("artifacts") or []:
                if a.get("url") and a["url"] not in seen_art:
                    seen_art.add(a["url"])
                    artifacts.append(a)

        try:
            wd = WEEKDAY[datetime.strptime(daykey, "%Y-%m-%d").weekday()]
            datestr = f"{daykey} ({wd})"
        except ValueError:
            datestr = daykey

        head = (f"# {datestr} · {plural(n_sessions, 'session')}"
                f" · {plural(len(groups), 'project')}")
        if multi_host:
            head += f" · {len(hosts)} machines"
        out.append(head)

        bits = [f"{tot_prompts} questions",
                f"{len(tot_files)} files (+{human_num(tot_add)}/-{human_num(tot_del)})",
                f"{human_time(tot_active)} at the keyboard"]
        if commits:
            bits.append(f"{len(commits)} commits")
        if tot_cost:
            bits.append(f"${tot_cost:.2f}")
        out.append("> " + " · ".join(bits) + "\n")

        for host, cwd in sorted(groups, key=lambda k: (Path(k[1]).name, k[0])):
            items = sorted(groups[(host, cwd)],
                           key=lambda p: p[1]["start"] or datetime.max.replace(tzinfo=timezone.utc))
            label = Path(cwd).name or cwd
            if multi_host:
                label += f" @ {host}"
            out.append(f"## {label}  `{cwd}`")

            branches = sorted({s["branch"] for s, _ in items
                               if s["branch"] and s["branch"] != "HEAD"})
            proj_files = Counter()
            for _, d in items:
                proj_files.update(d["files"])
            meta = []
            if branches:
                meta.append("branch " + ", ".join(branches))
            meta.append(f"{len(items)} sessions")
            if proj_files:
                meta.append(f"{len(proj_files)} files")
            out.append("_" + " · ".join(meta) + "_\n")

            for s, d in items:
                span = f"{local(d['start']):%H:%M}–{local(d['end']):%H:%M}" if d["start"] else "??:??"
                tag = "vscode" if s.get("entrypoint") == "claude-vscode" else (s.get("entrypoint") or "cli")
                out.append(f"### {span} · {human_time(d['active'])} · `{s['session_id'][:8]}` · {tag}")
                out.append(f"**{title_of(s)}**")

                if d["prompts"]:
                    out.append(f"- asked {len(d['prompts'])} times:")
                    for p in d["prompts"][:prompt_lines]:
                        out.append(f"  - {oneline(p, 100)}")
                    if len(d["prompts"]) > prompt_lines:
                        out.append(f"  - …and {len(d['prompts']) - prompt_lines} more")

                if d["files"]:
                    names = [rel(f, cwd) for f, _ in d["files"].most_common(8)]
                    more = f" (+{len(d['files']) - 8} more)" if len(d["files"]) > 8 else ""
                    diff = f" (+{human_num(d['added'])}/-{human_num(d['removed'])})" if (d["added"] or d["removed"]) else ""
                    out.append(f"- edited {len(d['files'])} files{diff}: {', '.join(names)}{more}")

                if d["scratch"]:
                    out.append(f"- plus {len(d['scratch'])} scratch scripts "
                               f"({', '.join(Path(f).name for f, _ in d['scratch'].most_common(4))})")

                landed = [c for t in d["turns"] for c in t["commits"]]
                if landed:
                    out.append("- commit: " + "; ".join(
                        f"`{c['short']}` {c['subject']}" for c in landed[:4]))
                elif d["commits"]:
                    # 指令跑過但 git log 對不到 (不是 repo, 或根本沒真的 commit 成功)
                    out.append("- tried to commit: "
                               + "; ".join(f'"{c}"' for c in d["commits"][:4]))

                cmds = dedupe_commands(d["commands"])
                if cmds:
                    out.append(f"- ran: {'; '.join(cmds)}")

                if d["slash"]:
                    out.append("- slash: " + ", ".join(
                        f"{k}×{v}" if v > 1 else k for k, v in d["slash"].most_common(6)))

                if d["ide_files"] and not d["files"]:
                    names = [rel(f, cwd) for f, _ in d["ide_files"].most_common(5)]
                    out.append(f"- opened in the IDE: {', '.join(names)}")

                if d["subagents"]:
                    out.append("- subagent: " + ", ".join(f"{k}×{v}" for k, v in d["subagents"].most_common()))

                busy = Counter({k: v for k, v in d["tools"].items() if k not in NOISE_TOOLS})
                if busy:
                    out.append("- tools: " + ", ".join(f"{k} x{v}" for k, v in busy.most_common(5)))

                if show_tokens:
                    out.append(f"- token: in {human_num(d['tok_in'])} / out {human_num(d['tok_out'])} "
                               f"/ cache {human_num(d['cache_read'])}"
                               + (f" · ${day_cost(s, daykey):.2f}" if s.get("cost_usd") else ""))
                out.append("")

        if commits or artifacts:
            out.append("---")
            if commits:
                out.append(f"**Commits ({len(commits)})**")
                for c in commits:
                    out.append(f"- `{c['short']}` {c['subject']} "
                               f"(+{c['added']}/-{c['removed']})")
            if artifacts:
                out.append(f"\n**Artifacts ({len(artifacts)})**")
                for a in artifacts:
                    out.append(f"- [{a.get('title') or 'artifact'}]({a.get('url')})")
            out.append("")

    return "\n".join(out).rstrip() + "\n"
