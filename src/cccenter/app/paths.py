"""
Where everything is: the code, the page, and the state this machine keeps.

Two roots that must not be confused. The repository holds the code and the web
page and is read-only at runtime; `~/.cc-center` holds the database, the
settings and the log, and survives a `git clean`. Point `CC_CENTER_STATE`
somewhere else to run a second copy without touching the first --- the tests do
exactly that.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..env import load_env

PACKAGE = Path(__file__).resolve().parent.parent        # src/cccenter
SRC = PACKAGE.parent                                    # src
ROOT = SRC.parent                                       # the repository

load_env()   # ROOT/.env: 個人設定 (機器清單、時區…) 放這裡, 不進版控。下面才開始讀環境變數。

# 遠端 agent 就是這個檔 —— 用 stdin 餵給對面的 python3, 所以它必須自成一檔。
SCANNER = PACKAGE / "scanner.py"


def _web_root() -> Path:
    """前端在哪。裝到別的地方 (pip install) 時用 CC_CENTER_WEB 指過去。"""
    env = os.environ.get("CC_CENTER_WEB")
    if env:
        return Path(env).expanduser()
    for base in (ROOT, PACKAGE, PACKAGE.parent):
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
