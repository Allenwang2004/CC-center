"""
Where everything is: the code, the page, and the state this machine keeps.

Two roots that must not be confused. The repository holds the code and the web
page and is read-only at runtime; `~/.cc-center` holds the database, the
settings and the log, and survives a `git clean`. Point `CC_CENTER_STATE`
somewhere else to run a second copy without touching the first --- the tests do
exactly that.

Inside the desktop app the code is a PyInstaller bundle (`desktop/`): there is
no repository, `sys._MEIPASS` is the unpacked bundle, and `scanner.py` and
`web/` are copied into it at the same relative places, so the paths below still
resolve. `FROZEN` says which of the two worlds this is.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ..env import load_env

FROZEN = bool(getattr(sys, "frozen", False))            # PyInstaller 包起來的 sidecar
BUNDLE = Path(getattr(sys, "_MEIPASS", "")) if FROZEN else None

PACKAGE = Path(__file__).resolve().parent.parent        # src/cccenter
SRC = PACKAGE.parent                                    # src
ROOT = BUNDLE if BUNDLE else SRC.parent                 # the repository (or the bundle)

load_env()   # ROOT/.env: 個人設定 (機器清單、時區…) 放這裡, 不進版控。下面才開始讀環境變數。

# 遠端 agent 就是這個檔 —— 用 stdin 餵給對面的 python3, 所以它必須自成一檔。
# 包成 sidecar 時 build 腳本把它放在 bundle 裡同樣的相對位置。
SCANNER = (BUNDLE / "cccenter" / "scanner.py") if BUNDLE else PACKAGE / "scanner.py"


def _web_root() -> Path:
    """前端在哪。裝到別的地方 (pip install) 時用 CC_CENTER_WEB 指過去。"""
    env = os.environ.get("CC_CENTER_WEB")
    if env:
        return Path(env).expanduser()
    for base in ([BUNDLE] if BUNDLE else []) + [ROOT, PACKAGE, PACKAGE.parent]:
        if (base / "web" / "index.html").is_file():
            return base / "web"
    return ROOT / "web"


WEB = _web_root()

STATE_DIR = Path(os.environ.get("CC_CENTER_STATE", Path.home() / ".cc-center"))
DB_FILE = STATE_DIR / "cc-center.db"
PID_FILE = STATE_DIR / "app.json"          # {pid, port, url, started}
LOG_FILE = STATE_DIR / "app.log"
SETTINGS_FILE = STATE_DIR / "settings.json"
DRAFT_DIR = STATE_DIR / "drafts"           # 工具自己叫 claude 產草稿留下的 transcript
HOSTS_FILE = Path.home() / ".cc-center-hosts"      # 跟 bin/cc-center-all 共用

LABEL = "com.cccenter.app"                 # launchd / systemd 的名字
