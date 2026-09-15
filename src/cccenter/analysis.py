"""
Derived numbers: what the raw scan means once you put a day, a project and a
git log next to each other.

`scanner` says what happened; this says what it adds up to. Kept apart from the
renderers because the web interface and the Markdown reports have to agree ---
they both read `day_changes`, so there is only ever one answer to "what changed
on this day".
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .scanner import (
    BORING_CMDS,
    NOTABLE_CMDS,
    git_status,
    local,
    oneline,
    parse_ts,
    rel,
)


def day_cost(s, key):
    """session 總花費按當天 output token 比例攤到各天。"""
    if s.get("cost_usd") in (None, 0):
        return 0.0
    total = sum(d["tok_out"] for d in s["days"].values()) or \
            sum(d["assistant_turns"] for d in s["days"].values())
    if not total:
        return 0.0
    mine = s["days"][key]["tok_out"] or s["days"][key]["assistant_turns"]
    return s["cost_usd"] * mine / total


def shorten_cmd(c: str) -> str:
    """內嵌腳本 (heredoc / -c) 只留呼叫本身, 不然整段 python 會洗版。"""
    for marker in ("<<", " -c ", " -e "):
        i = c.find(marker)
        if i > 0:
            return oneline(c[:i], 40) + " (inline script)"
    return oneline(c, 78)


def dedupe_commands(cmds, limit=6):
    seen, out = set(), []
    for c in cmds:
        parts = c.split()
        if not parts or parts[0] in BORING_CMDS or parts[0] not in NOTABLE_CMDS:
            continue
        short = shorten_cmd(c)
        k = " ".join(short.split()[:3])
        if k in seen:
            continue
        seen.add(k)
        out.append(short)
        if len(out) >= limit:
            break
    return out

def day_changes(sessions, cwd, day, orphans=None):
    """這天在這個專案改了什麼 —— 全部從已經收集到的資料算出來, 不猜也不生成。

    落地的以 commit 的 numstat 為準 (heredoc / inline script 寫的檔 transcript 看不到,
    commit 看得到)。agent 碰過但沒進任何 commit 的另外列 —— 那才是還沒收尾的東西。
    """
    rows = []
    for s in sessions:
        if (s.get("cwd") or "") != cwd:
            continue
        d = s["days"].get(day)
        if d:
            rows += [(t, s) for t in d["turns"]]
    rows.sort(key=lambda r: r[0]["ts"] or datetime.max.replace(tzinfo=timezone.utc))

    linked = [c for t, _ in rows for c in t["commits"]]
    loose = [c for c in (orphans or [])
             if (parse_ts(c.get("at")) and local(parse_ts(c["at"])).date().isoformat() == day)]
    committed = {f for c in linked + loose for f in c["files"]}

    touched = {}
    for i, (t, _) in enumerate(rows, 1):
        for fp, st in t["files"].items():
            e = touched.setdefault(rel(fp, cwd), {"a": 0, "d": 0, "turns": []})
            e["a"] += st["a"]
            e["d"] += st["d"]
            e["turns"].append(i)

    dirty = git_status(cwd)
    uncommitted = [{"path": rp, "still_dirty": rp in dirty, **e}
                   for rp, e in sorted(touched.items(), key=lambda kv: -(kv[1]["a"] + kv[1]["d"]))
                   if rp not in committed]

    # heredoc / inline script 寫的檔 transcript 看不到, 所以「有沒有動東西」不能只看 files ——
    # 跑過值得記的指令也算動過, 不然 git mv 那種會被歸到「只有查/試」。
    def did_something(t):
        return bool(t["files"]) or bool(dedupe_commands(t["commands"], 1))

    # 跟上面的 prompts 一樣只看真的提問, 不然這三個加起來會對不上那個總數
    asked_rows = [t for t, _ in rows if t["kind"] != "slash"]
    with_commit = sum(1 for t in asked_rows if t["commits"])
    touched_only = sum(1 for t in asked_rows if not t["commits"] and did_something(t))
    looked_only = sum(1 for t in asked_rows if not t["commits"] and not did_something(t))

    day_slices = [s["days"][day] for s in sessions
                  if (s.get("cwd") or "") == cwd and day in s["days"]]
    return {
        "day": day, "cwd": cwd,
        # 只算真的提問。/plugin, /exit 這種 slash 也是一列, 但不是「問了幾次」,
        # 不然這裡會比 session 自己的 prompt_count 多出來。
        "prompts": sum(1 for t, _ in rows if t["kind"] != "slash"),
        "active": sum(d["active"] for d in day_slices),
        "sessions": sorted({s["session_id"] for _, s in rows}),
        "commits": sorted(linked + loose, key=lambda c: c.get("at") or ""),
        "linked": len(linked), "loose": len(loose),
        "commit_files": len(committed),
        "commit_added": sum(c["added"] for c in linked + loose),
        "commit_removed": sum(c["removed"] for c in linked + loose),
        "uncommitted": uncommitted,
        "turn_stats": {"with_commit": with_commit, "touched_only": touched_only,
                       "looked_only": looked_only},
        "has_git": bool(cwd and Path(cwd, ".git").exists()),
    }

