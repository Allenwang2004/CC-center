"""
The facts a journal entry is written from: one day, one project, every question.

What goes in was settled by measuring, not guessing. Commands are nine tenths
of a day's characters and add nothing to the entry --- the same day written
without them reads the same. What the agent *said* is the other tenth and is
the entry: drop it and twenty-two questions come back as two. So this lays out
the prompt as typed, everything the agent said back, the files and the commits,
and of the commands only the few worth noting (the report's rule, plus anything
that wrote a file). A person reads the Sessions tab; `claude -p` reads this.
Nothing here is stored: it is recomputed from the transcripts every time, and
`cc-center-app journal --facts` prints it so you can see exactly what the model saw.

Long days are cut down in stages rather than truncated blindly: what the agent
said gets shorter first, the questions and the commits never do.

The layout is tagged (`<turn>`, `<asked>`, `<agent>`, `<ran>`) rather than
Markdown on purpose: what the agent said is itself Markdown, headings and all,
and a model reading a hundred thousand characters of it will happily carry on
in that voice unless every quoted block is fenced off unmistakably.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import re

from ..scanner import BORING_CMDS, NOTABLE_CMDS, human_time, local, oneline, parse_ts, rel

# 超過這個字數就開始縮。中文一字約一個 token; 20 萬字大約 6~10 萬 token, haiku 的
# 200k context 放得下, 而且離「讀到後面忘了前面」還有一段距離。
DEFAULT_BUDGET = 200_000

# 每一級把 agent 說的每一段縮到 (頭幾字, 尾幾字); None = 不縮
_LEVELS = [None, (1200, 300), (600, 0), (200, 0)]

COMMANDS_PER_TURN = 12
# 寫檔的指令: 重導向 (丟到 /dev/null 的不算)、sed -i、tee。`cat > x <<EOF` 是 cat
# 沒錯, 但它改了 x —— 這種改動「改了哪些檔」看不到, 只有指令裡有檔名, 不能跟 cat 一起丟。
_WRITES = re.compile(r"(^|\s)>{1,2}\s*(?!/dev/null|&)\S|\bsed\s+-i\b|\btee\b")
# 指令常常是 `cd /專案; 真正的指令`, 看第一個字要先把 cd 剝掉
_CD_PREFIX = re.compile(r"^\s*cd\s+\S+\s*(;|&&)\s*")


def _attr(v):
    return str(v).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def _cut_text(text, head, tail):
    if head is None or len(text) <= head + tail + 20:
        return text
    dropped = len(text) - head - tail
    return (text[:head].rstrip() + f"\n…（略 {dropped} 字）…\n"
            + (text[-tail:].lstrip() if tail else "")).rstrip()


def _short(c):
    """內嵌腳本 (heredoc / -c) 只留呼叫本身 —— 留到檔名看得到為止, 不像報告砍到 40 字。"""
    for marker in ("<<", " -c ", " -e "):
        i = c.find(marker)
        if i > 0:
            return oneline(c[:i], 120) + " (inline script)"
    return oneline(c, 160)


def worth_noting(cmds, limit=COMMANDS_PER_TURN):
    """跟報告同一套眼光 (analysis.dedupe_commands): 只看看的不記, 同一件事只記一次,
    內嵌腳本只留呼叫本身 —— 但寫檔的一律留下, 不管它叫什麼。"""
    seen, out = set(), []
    for c in cmds:
        body = _CD_PREFIX.sub("", c)
        parts = body.split()
        if not parts:
            continue
        writes = bool(_WRITES.search(body.split("<<", 1)[0]))
        if not writes and (parts[0] in BORING_CMDS or parts[0] not in NOTABLE_CMDS):
            continue
        short = _short(body)
        key = " ".join(short.split()[:3])
        if key in seen:
            continue
        seen.add(key)
        out.append(short)
        if len(out) >= limit:
            break
    return out


def _rows(sessions, cwd, day):
    rows = []
    for s in sessions:
        if (s.get("cwd") or "") != cwd:
            continue
        d = s["days"].get(day)
        if d:
            rows += [(t, s) for t in d["turns"]]
    rows.sort(key=lambda r: r[0]["ts"] or datetime.max.replace(tzinfo=timezone.utc))
    return rows


def _turn(t, s, cwd, i, level):
    clock = local(t["ts"]).strftime("%H:%M") if t["ts"] else "--:--"
    said_head, said_tail = level or (None, None)
    head = f'<turn n="{i}" at="{clock}" session="{s["session_id"][:8]}"'
    out = []
    if t["kind"] == "slash":
        out.append(head + f' slash="/{_attr(t["text"])}">')
    else:
        out.append(head + ">")
        out.append("<asked>")
        out.append((t["text"] or "").strip() or "(空)")
        out.append("</asked>")

    said = [x.strip() for x in (t.get("said") or []) if x.strip()]
    if said:
        out.append("<agent>")
        out.append("\n\n".join(_cut_text(x, said_head, said_tail) for x in said))
        out.append("</agent>")

    ran = worth_noting(t["commands"])
    if ran:
        out.append("<ran>")
        out += ran
        out.append("</ran>")

    if t["files"]:
        bits = []
        for fp, st in sorted(t["files"].items(), key=lambda kv: -(kv[1]["a"] + kv[1]["d"])):
            d = f" (+{st['a']} −{st['d']})" if (st["a"] or st["d"]) else ""
            bits.append(rel(fp, cwd) + d)
        out.append("<edited>" + ", ".join(bits) + "</edited>")

    for c in t["commits"]:
        out.append(f'<commit sha="{c["short"]}" added="{c["added"]}" removed="{c["removed"]}"'
                   f' files="{len(c["files"])}">{oneline(c["subject"], 120)}</commit>')

    if not said and not ran and not t["files"] and not t["commits"]:
        out.append("<nothing-done/>")
    out.append("</turn>")
    return out


def _render(sessions, cwd, day, orphans, level):
    rows = _rows(sessions, cwd, day)
    mine = [s for s in sessions if (s.get("cwd") or "") == cwd and s["days"].get(day)]
    asked = sum(1 for t, _ in rows if t["kind"] != "slash")
    active = sum(s["days"][day]["active"] for s in mine)
    branch = next((s["branch"] for s in mine if s.get("branch")), None)

    out = [f'<day date="{day}" project="{_attr(Path(cwd).name or cwd)}" path="{_attr(cwd)}"'
           + (f' branch="{_attr(branch)}"' if branch else "")
           + f' sessions="{len(mine)}" asked="{asked}" active="{human_time(active)}">']
    if not rows:
        out.append("<nothing-asked/>")
    for i, (t, s) in enumerate(rows, 1):
        out += _turn(t, s, cwd, i, level)

    rest = (orphans or {}).get(cwd) or []
    for c in sorted(rest, key=lambda c: c["at"]):
        when = local(parse_ts(c["at"])).strftime("%H:%M") if c.get("at") else "--:--"
        out.append(f'<commit-without-question sha="{c["short"]}" at="{when}"'
                   f' author="{_attr(c["author"])}">{oneline(c["subject"], 120)}'
                   "</commit-without-question>")
    out.append("</day>")
    return "\n".join(out) + "\n"


def render_journal_facts(sessions, cwd, day, orphans=None, budget=DEFAULT_BUDGET):
    """這天在這個專案發生的每一件事, 給 claude -p 讀的那份。

    太長就一級一級縮: 先縮 agent 說的話的中段, 再只留開頭; 提問跟 commit
    永遠完整。回傳 (文字, 縮了幾級)。
    """
    text, used = "", 0
    for used, level in enumerate(_LEVELS):
        text = _render(sessions, cwd, day, orphans, level)
        if len(text) <= budget:
            break
    return text, used
