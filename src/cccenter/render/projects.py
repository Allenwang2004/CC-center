"""
The by-project report: what you asked, what it changed, where it landed.

The journal is written from a fuller record than this (`render.journal`); this
is the one a person reads, so each question keeps only what is worth a glance.
"""

from __future__ import annotations

import socket
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ..analysis import dedupe_commands
from ..scanner import (
    WEEKDAY,
    human_num,
    human_time,
    local,
    oneline,
    parse_ts,
    rel,
)


def _turn_files_line(t, cwd):
    bits = []
    for fp, st in sorted(t["files"].items(), key=lambda kv: -(kv[1]["a"] + kv[1]["d"]))[:6]:
        d = f" (+{st['a']} −{st['d']})" if (st["a"] or st["d"]) else ""
        bits.append(rel(fp, cwd) + d)
    more = len(t["files"]) - 6
    return ", ".join(bits) + (f" and {more} more" if more > 0 else "")


def turn_lines(t, cwd, i, session_id=None, text_cap=220):
    """一題 → 幾行 Markdown。專案報告跟 journal 共用同一份, 免得兩邊長不一樣。"""
    clock = local(t["ts"]).strftime("%H:%M") if t["ts"] else "--:--"
    mark = "/" if t["kind"] == "slash" else ""
    out = [f"{i}. **{clock}** {mark}{oneline(t['text'], text_cap)}"]
    if t["files"]:
        out.append(f"   - edited: {_turn_files_line(t, cwd)}")
    cmds = dedupe_commands(t["commands"], 3)
    if cmds:
        out.append("   - ran: " + "; ".join(f"`{c}`" for c in cmds))
    for c in t["commits"]:
        out.append(f"   - commit `{c['short']}` — {oneline(c['subject'], 90)}"
                   f" (+{c['added']} −{c['removed']}, {len(c['files'])} 檔)")
    if not t["files"] and not t["commits"] and not cmds:
        out.append("   - (nothing was written)")
    if session_id:
        out.append(f"   - session `{session_id[:8]}`")
    return out


def render_projects(sessions, orphans, text_cap=220):
    """按專案排, 每個專案底下一題一題列: 你問了什麼 → 改了什麼 → 進了哪個 commit。"""
    projects = defaultdict(list)
    for s in sessions:
        projects[s["cwd"] or "(unknown)"].append(s)
    if not projects and not orphans:
        return "No sessions in this range."

    def proj_last(cwd):
        return max((d["end"] for s in projects[cwd] for d in s["days"].values() if d["end"]),
                   default=datetime.min.replace(tzinfo=timezone.utc))

    out = ["# What happened in each project", ""]
    for cwd in sorted(projects, key=proj_last, reverse=True):
        group = projects[cwd]
        # (day -> [(session, dayslice)])
        days = defaultdict(list)
        for s in group:
            for key, day in s["days"].items():
                days[key].append((s, day))

        turns = [t for s in group for day in s["days"].values() for t in day["turns"]]
        commits = [c for t in turns for c in t["commits"]]
        n_add = sum(d["added"] for _, ds in days.items() for _, d in ds)
        n_del = sum(d["removed"] for _, ds in days.items() for _, d in ds)
        active = sum(d["active"] for _, ds in days.items() for _, d in ds)
        hosts = sorted({s["host"] for s in group})
        branch = next((s["branch"] for s in group if s.get("branch")), None)

        out.append(f"## {Path(cwd).name or cwd}")
        meta = [f"`{cwd}`"]
        if branch:
            meta.append(f"branch `{branch}`")
        if len(hosts) > 1 or hosts[:1] not in ([], [socket.gethostname().split(".")[0]]):
            meta.append(" / ".join(hosts))
        out.append(" · ".join(meta))
        # slash turn 不是提問, 跟 day_changes 和介面上的 "asked" 對齊
        n_asked = sum(1 for t in turns if t["kind"] != "slash")
        stat = [f"{n_asked} questions", f"{len(group)} sessions",
                f"{human_time(active)} at the keyboard"]
        if n_add or n_del:
            stat.append(f"+{human_num(n_add)} −{human_num(n_del)}")
        stat.append(f"{len(commits)} commits")
        out.append("· ".join(x + " " for x in stat).strip())
        out.append("")

        for daykey in sorted(days, reverse=True):
            dt = datetime.strptime(daykey, "%Y-%m-%d").date()
            out.append(f"### {daykey} ({WEEKDAY[dt.weekday()]})")
            rows = [(t, s) for s, d in days[daykey] for t in d["turns"]]
            rows.sort(key=lambda r: r[0]["ts"] or datetime.max.replace(tzinfo=timezone.utc))
            if not rows:
                out.append("- (no questions, only tool activity)")
                out.append("")
                continue
            for i, (t, s) in enumerate(rows, 1):
                out += turn_lines(t, cwd, i, s["session_id"], text_cap)
            out.append("")

        rest = orphans.get(cwd) or []
        if rest:
            out.append(f"**Commits with no question behind them ({len(rest)})** — "
                       "made by hand, or outside the agent")
            for c in sorted(rest, key=lambda c: c["at"], reverse=True):
                when = local(parse_ts(c["at"])).strftime("%m-%d %H:%M")
                out.append(f"- `{c['short']}` {when} {oneline(c['subject'], 80)}"
                           f" — {c['author']}")
            out.append("")

    lonely = {k: v for k, v in orphans.items() if k not in projects}
    if lonely:
        out.append("## Repos with no sessions")
        for cwd, rest in lonely.items():
            out.append(f"### {Path(cwd).name or cwd}")
            for c in sorted(rest, key=lambda c: c["at"], reverse=True):
                when = local(parse_ts(c["at"])).strftime("%m-%d %H:%M")
                out.append(f"- `{c['short']}` {when} {oneline(c['subject'], 80)} — {c['author']}")
            out.append("")
    return "\n".join(out).rstrip() + "\n"
