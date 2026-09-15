"""
Is this session waiting for you?

The transcript says where a session stopped; the process list says whether
anything is still there to carry it on. Neither answers the question alone ---
a session that stopped mid-tool with no `claude` left running is not waiting for
you, it died; a session that stopped talking while its process is still up is.

Pure logic, no I/O: hand it a session, the settings, a clock and what the
watcher believes about `alive`, and it says what to call it, or nothing at all.
"""

from __future__ import annotations

from .util import now, parse_iso


# 這些工具幾乎不會自己跑很久, 卡住通常就是在等你按同意
PERMISSION_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Update", "Artifact"}
# `claude -p` / Agent SDK 的一次性執行。它印完就結束, 沒有人坐在前面等你回話,
# 也沒有東西可以 resume —— 所以它永遠不該變成一張「在等你」的卡。
NON_INTERACTIVE = ("sdk",)
ATTENTION_LABEL = {"waiting": "Waiting for your reply",
                   "permission": "Probably waiting for permission",
                   "stuck": "A tool has been stuck a long time",
                   "died": "Stopped part-way through"}


def attention_of(sess, st, ref=None, alive=None):
    """這個 session 是不是在等你? 回 None 表示不用管它。

    alive: 這台機器上還有沒有 claude process 蹲在這個 cwd。
      True  —— 有東西能繼續, 停下來就是真的在等你
      False —— 沒有 process 了, 「停在那」只是結束了, 不是在等你
      None  —— 不知道 (那台收不到, 或遠端關著), 就照舊只看 transcript
    """
    tail = sess.get("tail")
    if not tail:
        return None
    if (sess.get("entrypoint") or "").startswith(NON_INTERACTIVE):
        return None
    ref = ref if ref is not None else now()
    since = parse_iso(tail.get("since"))
    last = parse_iso(tail.get("at")) or since
    if since is None or last is None:
        return None
    age = ref - since
    if ref - last > float(st["attention_max"]):
        return None                       # 太久以前的事了, 不算「現在在等你」
    state, tool = tail.get("state"), tail.get("tool")
    kind = None
    if alive is False:
        # process 都沒了: 停在工具中間 = 被砍掉/掛了, 其他情況就只是結束了
        if state == "tool" and age >= float(st["notify_tool_after"]):
            kind = "died"
    elif state == "waiting" and age >= float(st["notify_waiting_after"]):
        kind = "waiting"
    elif state == "tool" and tool in PERMISSION_TOOLS and age >= float(st["notify_tool_after"]):
        kind = "permission"
    elif state == "tool" and age >= float(st["notify_stuck_after"]):
        kind = "stuck"
    if not kind:
        return None
    return {"kind": kind, "label": ATTENTION_LABEL[kind], "tool": tool,
            "age": round(age), "since": tail.get("since"), "alive": alive,
            "interrupted": bool(tail.get("interrupted"))}
