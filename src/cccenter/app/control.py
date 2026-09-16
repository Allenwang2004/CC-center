"""
Reaching back into a session: which ones are live, and stopping one.

The watcher only ever read. This is the one place it acts on a session, and it
does the smallest thing that works: a signal to the `claude` process. SIGINT is
what Ctrl+C in the terminal sends --- the current turn stops, the conversation
stays open; SIGTERM ends the process (the session can still be resumed later).
On another machine the same signal goes over ssh.

A process is matched to a session by folder (see `monitor.alive_of`), so when
two `claude` are running in the same folder there is no honest way to know
which is which. Then nothing is sent and the caller is told the pids instead.

`live()` is the short list the menu bar shows: the same two groups as the
Agents tab, with only the fields a menu item needs.
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

from .. import scanner
from .attention import attention_of
from .util import now, parse_iso

SIGNALS = {"INT": signal.SIGINT, "TERM": signal.SIGTERM}


def pids_for(sess, procs):
    """這個 session 的 cwd 底下、而且在它最後一筆之前就活著的 claude。

    procs 是那台機器的 process 清單 (scanner.running_claude 的格式); None 表示
    那台收不到。回傳 pid 的 list, 可能是空的、也可能不只一個。
    """
    if procs is None:
        return []
    cwd = (sess.get("cwd") or "").rstrip("/")
    if not cwd:
        return []
    here = [p for p in procs if (p.get("cwd") or "").rstrip("/") == cwd and p.get("pid")]
    last = parse_iso((sess.get("tail") or {}).get("at"))
    if last is not None:
        older = [p for p in here if p.get("started") is None or p["started"] <= last]
        if older:
            here = older
    return [int(p["pid"]) for p in here]


def signal_session(sess, procs, sig, local_host, timeout=8,
                   kill=os.kill, run=subprocess.run):
    """送一個訊號給這個 session 的 claude。回傳 {"pid", "signal"}; 送不了就 raise ValueError。"""
    if sig not in SIGNALS:
        raise ValueError(f"signal must be INT or TERM, got {sig!r}")
    pids = pids_for(sess, procs)
    host = sess.get("host") or local_host
    if not pids:
        raise ValueError("No claude process found for this session on "
                         f"{host}; it may have ended already.")
    if len(pids) > 1:
        raise ValueError(f"{len(pids)} claude processes are running in {sess.get('cwd')} "
                         f"on {host} and there is no way to tell which is this session. "
                         f"From a terminal: kill -{sig} " + " ".join(map(str, pids)))
    pid = pids[0]
    if host == local_host:
        try:
            kill(pid, SIGNALS[sig])
        except ProcessLookupError as e:
            raise ValueError(f"claude (pid {pid}) is already gone.") from e
        except PermissionError as e:
            raise ValueError(f"Not allowed to signal pid {pid}.") from e
    else:
        p = run(["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}", host,
                 f"kill -{sig} {pid}"], capture_output=True, timeout=timeout + 10)
        if p.returncode != 0:
            err = (p.stderr or b"").decode("utf-8", "replace").strip()
            raise ValueError(f"Could not signal pid {pid} on {host}: {err or 'ssh failed'}")
    return {"pid": pid, "signal": sig, "host": host}


def live(sessions, st, alive_of, ref=None):
    """狀態列選單要的那兩組: 在等你的、在跑的。跟 Agents 分頁同一個定義。"""
    ref = ref if ref is not None else now()
    window = float(st.get("live_window") or 600)
    waiting, running = [], []
    for s in sessions:
        alive = alive_of(s)
        a = attention_of(s, st, ref, alive)
        last = parse_iso((s.get("tail") or {}).get("at")) or 0
        item = {"session_id": s.get("session_id"), "host": s.get("host"),
                "cwd": s.get("cwd"), "name": Path(s.get("cwd") or "").name or "?",
                "title": scanner.title_of(s), "last": last}
        if a:
            item["label"] = a["label"]
            waiting.append(item)
        elif alive is not False and ref - last < window:
            running.append(item)
    waiting.sort(key=lambda x: -x["last"])
    running.sort(key=lambda x: -x["last"])
    return {"waiting": waiting, "running": running}
