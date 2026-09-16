"""
Your CLAUDE.md files, edited where Claude Code reads them.

Two kinds, both plain files and nothing else --- no database, no copy anywhere:

    ~/.claude/CLAUDE.md                 the global one, per machine
    <project>/.claude/CLAUDE.md         one per project

The page lists every machine's global file and every project's file, shows the
ones that exist and offers to create the ones that do not. Reads and writes go
straight to disk, over ssh for a project that lives on another machine, so what
Claude Code sees next time is exactly what the page showed.

Remote files are read in one round trip per machine: a tiny Python program is
run over there with the paths as arguments and answers with JSON. Standard
library only, like everything else.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

from .util import shell_quote

GLOBAL_REMOTE = "~/.claude/CLAUDE.md"     # 遠端: 交給對面的 python 展開 ~
PROJECT_REL = ".claude/CLAUDE.md"
LIMIT = 512 * 1024                        # 大於這個不是設定檔, 別整份塞進頁面

# 在遠端跑的讀檔程式: argv 是路徑, 印一個 {path: {exists, body, mtime}} 回來。
READER = (
    "import json,os,sys\n"
    "o={}\n"
    "for p in sys.argv[1:]:\n"
    "  q=os.path.expanduser(p)\n"
    "  try:\n"
    "    with open(q,encoding='utf-8',errors='replace') as f: b=f.read()\n"
    "    o[p]={'exists':True,'body':b,'mtime':os.path.getmtime(q)}\n"
    "  except FileNotFoundError: o[p]={'exists':False}\n"
    "  except OSError as e: o[p]={'exists':False,'error':str(e)}\n"
    "print(json.dumps(o))\n"
)


def global_path_local() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "CLAUDE.md"


def project_path(cwd: str) -> str:
    return f"{cwd.rstrip('/')}/{PROJECT_REL}"


def _read_local(paths):
    out = {}
    for p in paths:
        q = Path(os.path.expanduser(p))
        try:
            body = q.read_text(encoding="utf-8", errors="replace")
            out[p] = {"exists": True, "body": body[:LIMIT], "mtime": q.stat().st_mtime}
        except FileNotFoundError:
            out[p] = {"exists": False}
        except OSError as e:
            out[p] = {"exists": False, "error": str(e)}
    return out


def _read_remote(host, paths, timeout, run=subprocess.run):
    """一台機器一次 ssh: python3 -c READER path…"""
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}", host,
           "python3 -c " + shell_quote(READER) + " " + " ".join(shell_quote(p) for p in paths)]
    try:
        p = run(cmd, capture_output=True, timeout=timeout + 20)
    except (OSError, subprocess.SubprocessError) as e:
        return {q: {"exists": False, "error": f"{type(e).__name__}"} for q in paths}
    if p.returncode != 0:
        err = (p.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        msg = err[-1] if err else "ssh failed"
        return {q: {"exists": False, "error": msg} for q in paths}
    try:
        data = json.loads((p.stdout or b"").decode("utf-8", "replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {q: {"exists": False, "error": "unreadable answer"} for q in paths}
    for v in data.values():
        if isinstance(v, dict) and isinstance(v.get("body"), str):
            v["body"] = v["body"][:LIMIT]
    return {q: data.get(q) or {"exists": False, "error": "no answer"} for q in paths}


def collect(local_host, hosts, projects, timeout=8, run=subprocess.run):
    """每台機器的全域檔 + 每個專案的檔。

    projects 是 monitor.project_list() 那份 ({cwd, host, exists…}); 資料夾不在了的
    專案不列 (沒有地方可以放檔)。回傳的每一筆:
    {scope: global|project, host, cwd, path, exists, body, mtime, error}
    """
    wanted: dict[str, list] = {}           # host -> [(scope, cwd, path, meta)]
    wanted.setdefault(local_host, []).append(("global", None, str(global_path_local()), None))
    for h in hosts:
        if h != local_host:
            wanted.setdefault(h, []).append(("global", None, GLOBAL_REMOTE, None))
    seen = set()
    for pr in projects:
        cwd, host = pr.get("cwd"), pr.get("host") or local_host
        if not cwd or not pr.get("exists") or (host, cwd) in seen:
            continue
        if host != local_host and host not in hosts:
            continue                       # 關掉的機器, 讀不到也別去撞
        seen.add((host, cwd))
        wanted.setdefault(host, []).append(("project", cwd, project_path(cwd), pr))

    answers: dict[str, dict] = {}
    lock = threading.Lock()

    def one(host, items):
        paths = [it[2] for it in items]
        got = _read_local(paths) if host == local_host else _read_remote(host, paths, timeout, run)
        with lock:
            answers[host] = got

    threads = [threading.Thread(target=one, args=(h, items), daemon=True)
               for h, items in wanted.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    out = []
    for host, items in wanted.items():
        got = answers.get(host) or {}
        for scope, cwd, path, meta in items:
            r = got.get(path) or {"exists": False, "error": "not read"}
            out.append({"scope": scope, "host": host, "cwd": cwd, "path": path,
                        "exists": bool(r.get("exists")),
                        "body": r.get("body") or "",
                        "mtime": r.get("mtime"),
                        "error": r.get("error"),
                        "last_active": (meta or {}).get("last_active") or 0})
    order = {"global": 0, "project": 1}
    out.sort(key=lambda f: (order[f["scope"]], f["host"] != local_host,
                            -(f["last_active"] or 0), f["cwd"] or ""))
    return out


def write(local_host, host, cwd, text, timeout=8, run=subprocess.run):
    """存一份。本機直接寫; 遠端 ssh mkdir -p && cat >。回傳寫到哪。"""
    text = (text or "")
    if text and not text.endswith("\n"):
        text += "\n"
    host = host or local_host
    if host == local_host:
        target = Path(project_path(cwd)) if cwd else global_path_local()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return str(target)
    if cwd:
        target = project_path(cwd)
        d, f = shell_quote(f"{cwd.rstrip('/')}/.claude"), shell_quote(target)
    else:
        target = GLOBAL_REMOTE
        d, f = '"$HOME/.claude"', '"$HOME/.claude/CLAUDE.md"'
    p = run(["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}", host,
             f"mkdir -p {d} && cat > {f}"],
            input=text.encode("utf-8"), capture_output=True, timeout=timeout + 40)
    if p.returncode != 0:
        err = (p.stderr or b"").decode("utf-8", "replace").strip()
        raise OSError(f"could not write on {host}: {err or 'ssh failed'}")
    return target
