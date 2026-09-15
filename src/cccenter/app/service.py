"""
Starting on login: launchd on macOS, systemd --user on Linux.

Both write a unit that runs `python -m cccenter.app serve` and restart it if it
dies. The generated file records absolute paths, so re-run `install` after moving
the checkout.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .daemon import child_env
from .paths import LABEL, LOG_FILE, ROOT, STATE_DIR


PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>-m</string>
    <string>cccenter.app</string>
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
  <dict>
    <key>PATH</key><string>{path}</string>
    <key>PYTHONPATH</key><string>{pypath}</string>
  </dict>
</dict>
</plist>
"""

UNIT = """[Unit]
Description=cc-center app

[Service]
ExecStart={python} -m cccenter.app serve --port {port} --no-open
WorkingDirectory={cwd}
Restart=always
Environment=PATH={path}
Environment=PYTHONPATH={pypath}

[Install]
WantedBy=default.target
"""


def cmd_install(args):
    py = sys.executable
    env_path = os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin")
    pypath = child_env().get("PYTHONPATH", "")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        target = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(PLIST.format(label=LABEL, python=py, port=args.port,
                                       cwd=str(ROOT), log=str(LOG_FILE),
                                       path=env_path, pypath=pypath),
                          encoding="utf-8")
        subprocess.run(["launchctl", "unload", str(target)], capture_output=True)
        r = subprocess.run(["launchctl", "load", str(target)], capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr.strip(), file=sys.stderr)
            return 1
        print(f"裝好了: {target}\n開機會自動起來, 掛掉也會自己重開。")
        return 0
    target = Path.home() / ".config/systemd/user" / f"{LABEL}.service"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(UNIT.format(python=py, port=args.port, cwd=str(ROOT),
                                  path=env_path, pypath=pypath), encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "--user", "enable", "--now", f"{LABEL}.service"], check=False)
    print(f"裝好了: {target}")
    return 0


def cmd_uninstall(_args):
    if sys.platform == "darwin":
        target = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
        if target.exists():
            subprocess.run(["launchctl", "unload", str(target)], capture_output=True)
            target.unlink()
            print(f"移掉了 {target}")
            return 0
    else:
        target = Path.home() / ".config/systemd/user" / f"{LABEL}.service"
        if target.exists():
            subprocess.run(["systemctl", "--user", "disable", "--now", f"{LABEL}.service"],
                           check=False)
            target.unlink()
            print(f"移掉了 {target}")
            return 0
    print("本來就沒裝")
    return 0
