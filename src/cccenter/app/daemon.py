"""
Starting, stopping and finding the background server.

The pidfile in `~/.cc-center/app.json` is what "is it already running?" means:
it holds the pid, the port and the URL, and a pid that no longer exists counts as
not running. `start` is therefore safe to run twice --- the second one finds the
first and just opens the browser.

The child is launched as `python -m cccenter.app` with `src` on PYTHONPATH, so
the same command works whether this is a checkout or an installed package.
"""

from __future__ import annotations

import errno
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from .desktop import open_url
from .paths import LOG_FILE, PID_FILE, ROOT, SRC, STATE_DIR
from .util import now


def child_command(*args) -> list[str]:
    """再開一份自己 (背景 serve)。裝過或沒裝過都走同一條路。"""
    return [sys.executable, "-m", "cccenter.app", *args]


def child_env() -> dict:
    env = dict(os.environ)
    if (SRC / "cccenter" / "__init__.py").is_file():
        env["PYTHONPATH"] = os.pathsep.join(
            [str(SRC)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return env


def write_pidfile(port, url, started) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(json.dumps({"pid": os.getpid(), "port": port, "url": url,
                                    "started": started}, indent=2), encoding="utf-8")


def clear_pidfile() -> None:
    try:
        PID_FILE.unlink()
    except OSError:
        pass


def read_pidfile():
    try:
        info = json.loads(PID_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    pid = info.get("pid")
    if not pid:
        return None
    try:
        os.kill(pid, 0)
    except OSError as e:
        if e.errno == errno.ESRCH:
            return None
        if e.errno != errno.EPERM:
            return None
    return info


def ping(url) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(url + "healthz", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def cmd_start(args, quiet=False):
    info = read_pidfile()
    if info:
        if not quiet:
            print(f"已經在跑了 → {info['url']} (pid {info['pid']})")
        if not args.no_open:
            open_url(info["url"])
        return 0
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log = open(LOG_FILE, "a", buffering=1)
    log.write(f"\n=== start {datetime.now():%F %T} ===\n")
    subprocess.Popen(
        child_command("serve", "--port", str(args.port), "--bind", args.bind, "--no-open"),
        stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True, cwd=str(ROOT), env=child_env())
    for _ in range(80):
        time.sleep(0.1)
        info = read_pidfile()
        if info and ping(info["url"]):
            print(f"背景啟動 → {info['url']} (pid {info['pid']})")
            print(f"log: {LOG_FILE}")
            if not args.no_open:
                open_url(info["url"])
            return 0
    print(f"起不來, 看看 {LOG_FILE}", file=sys.stderr)
    return 1


def cmd_stop(_args):
    info = read_pidfile()
    if not info:
        print("沒在跑")
        return 0
    pid = info["pid"]
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        print(f"停不掉: {e}", file=sys.stderr)
        return 1
    # 等 process 真的死掉再回去, 不然接著 start 會撞到還沒放掉的 port
    for _ in range(80):
        time.sleep(0.1)
        try:
            os.kill(pid, 0)
        except OSError:
            break
    print(f"停了 (pid {pid})")
    return 0


def cmd_status(_args):
    info = read_pidfile()
    if not info:
        print("沒在跑")
        return 1
    alive = ping(info["url"])
    up = int(now() - info.get("started", now()))
    print(f"{'在跑' if alive else '有 pid 但沒回應'} → {info['url']}")
    print(f"pid {info['pid']} · 起來 {up // 3600}h{up % 3600 // 60}m · log {LOG_FILE}")
    return 0 if alive else 1


def cmd_open(_args):
    info = read_pidfile()
    if not info:
        print("沒在跑, 先 bin/cc-center-app")
        return 1
    open_url(info["url"])
    return 0


def cmd_logs(args):
    if not LOG_FILE.exists():
        print("還沒有 log")
        return 1
    if args.follow:
        subprocess.run(["tail", "-f", str(LOG_FILE)])
    else:
        subprocess.run(["tail", "-n", "60", str(LOG_FILE)])
    return 0


PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>{script}</string>
    <string>serve</string>
    <string>--port</string><string>{port}</string>
    <string>--no-open</string>
  </array>
  <key>WorkingDirectory</key><string>{cwd}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>{path}</string></dict>
</dict>
</plist>
"""

UNIT = """[Unit]
Description=cc-center app

[Service]
ExecStart={python} {script} serve --port {port} --no-open
WorkingDirectory={cwd}
Restart=always
Environment=PATH={path}

[Install]
WantedBy=default.target
"""
