"""
Talking to the machine you are sitting at: a notification, or a browser window.

Best effort by design. If `terminal-notifier` is missing we fall back to
osascript, if that is missing nothing happens --- a missed notification is never
worth an exception in the watch loop.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import webbrowser

from .paths import LABEL
from .settings import read_settings


def notify(title, subtitle, body, sound=False, url=None):
    """跳一則系統通知; 有 terminal-notifier 的話點了可以直接開介面。"""
    if sys.platform == "darwin":
        tn = shutil.which("terminal-notifier")
        if tn:
            args = [tn, "-title", title, "-subtitle", subtitle, "-message", body,
                    "-group", LABEL]
            if url:
                args += ["-open", url]
            if sound:
                args += ["-sound", "Ping"]
            subprocess.run(args, capture_output=True)
            return
        q = lambda t: '"' + str(t).replace("\\", "").replace('"', "'") + '"'
        script = (f"display notification {q(body)} with title {q(title)} "
                  f"subtitle {q(subtitle)}" + (' sound name "Ping"' if sound else ""))
        subprocess.run(["osascript", "-e", script], capture_output=True)
    elif shutil.which("notify-send"):
        subprocess.run(["notify-send", f"{title} — {subtitle}", body], capture_output=True)


def open_url(url):
    """用設定裡的瀏覽器開; 沒設定或開不起來就回到系統預設。"""
    name = (read_settings().get("browser") or "").strip()
    if name and sys.platform == "darwin":
        if subprocess.run(["open", "-a", name, url], capture_output=True).returncode == 0:
            return
    if name and sys.platform.startswith("linux"):
        if subprocess.run([name, url], capture_output=True).returncode == 0:
            return
    webbrowser.open(url)
