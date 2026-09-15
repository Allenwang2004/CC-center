"""
What you chose, and which machines to ask.

Settings live in `~/.cc-center/settings.json` and are read on every use rather
than cached: the interface writes them, the watch loop reads them, and a value
you change takes effect on the next tick without a restart. Unknown keys are
dropped on write, so a stale file cannot smuggle in a setting the code no longer
understands.

The machine list is a plain text file shared with `bin/cc-center-all`, one host
per line, so the shell script and the app never disagree about who to ask.

Nothing personal is hard-coded here. The first-run defaults --- which machines,
which time zone, which browser --- come from the environment (`CC_HOSTS`,
`CC_TZ`, `CC_BROWSER`, `CC_SSH_TIMEOUT`), and the environment is filled in from
`.env` at the repository root; see `.env.example`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from .. import scanner
from .paths import HOSTS_FILE, SETTINGS_FILE, STATE_DIR


def local_tz_name() -> str:
    """這台機器的時區。找得到 IANA 名稱 (Asia/Taipei) 就用名稱, 不然退成固定位移 (+08:00)。"""
    env = os.environ.get("TZ")
    if env and "/" in env:
        return env
    try:
        target = os.readlink("/etc/localtime")          # …/zoneinfo/Asia/Taipei
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    off = datetime.now().astimezone().strftime("%z")
    return f"{off[:3]}:{off[3:]}"


def _int_env(name: str, fallback: int) -> int:
    try:
        return int(os.environ.get(name, "") or fallback)
    except ValueError:
        return fallback


DEFAULT_SETTINGS = {
    "tz": os.environ.get("CC_TZ") or local_tz_name(),
    "days": 1,
    "date": "",
    "include_local": True,
    "disabled_hosts": [],              # 有勾的才收
    "sidechains": False,
    "oneshot": False,                  # claude -p / SDK 的一次性執行算不算一段工作
    "tokens": False,
    "prompts": 5,
    "entrypoints": [],
    "ssh_timeout": _int_env("CC_SSH_TIMEOUT", 8),
    "remote_enabled": True,            # 關掉就完全不碰遠端 (下班連不到的時候)
    "sidebar_w": 340,
    "local_poll": 12,                  # 秒; 0 = 關掉自動重掃
    "remote_poll": 300,                # 秒; 0 = 遠端只在手動按的時候收
    "remote_poll_hot": 60,             # 剛剛還有人在動的機器收快一點
    "live_window": 600,                # 秒; 這段時間內有動作就算「進行中」
    "out_dir": str(Path.home() / "Documents" / "cc-center"),
    "theme": "auto",
    "browser": os.environ.get("CC_BROWSER", ""),   # macOS 的 app 名稱, 例如 Arc; 空的 = 系統預設
    # 「在等你」的通知
    "notify_scope": "remote",          # off | remote | all
    "notify_sound": True,
    "notify_waiting_after": 45,        # assistant 講完話幾秒後算在等你
    "notify_tool_after": 90,           # 檔案類工具發出去幾秒還沒回來 = 大概在等權限
    "notify_stuck_after": 900,         # 任何工具卡這麼久就講一聲
    "attention_max": 14400,            # 超過這麼久的就不再提醒 (預設 4 小時)
    # 每天早上自動把昨天的 journal 寫好 (app/journal.py)
    "journal_auto": True,
    "journal_at": "06:00",             # 本機時間過了這一刻、還沒產齊就跑 (睡醒補)
    "journal_model": "haiku",          # claude -p --model; 空的 = claude 自己的預設
    "journal_input_max": 200000,       # 餵給 claude 的紀錄超過這麼多字就開始縮
}


def migrate_old_names():
    """從舊名字 (cc-daily) 搬過來; 已經搬過就什麼都不做。

    一個一個檔案搬, 不是整個資料夾 rename —— 新資料夾可能已經被建出來了。
    """
    old_hosts = Path.home() / ".cc-daily-hosts"
    if old_hosts.is_file() and not HOSTS_FILE.exists():
        try:
            old_hosts.rename(HOSTS_FILE)
            print(f"搬過來了: {old_hosts} → {HOSTS_FILE}", flush=True)
        except OSError:
            pass

    old_dir = Path.home() / ".cc-daily"
    if old_dir.is_dir():
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        for name in ("settings.json",):          # log 跟 pidfile 不用搬
            src, dst = old_dir / name, STATE_DIR / name
            if src.is_file() and not dst.exists():
                try:
                    src.rename(dst)
                    print(f"搬過來了: {src} → {dst}", flush=True)
                except OSError:
                    pass

    # 匯出資料夾如果還指著舊的預設值, 也一起換掉
    if SETTINGS_FILE.is_file():
        stale = str(Path.home() / "Documents" / "cc-daily")
        if read_settings().get("out_dir") == stale:
            write_settings({"out_dir": DEFAULT_SETTINGS["out_dir"]})


def read_hosts():
    if HOSTS_FILE.is_file():
        lines = [ln.strip() for ln in HOSTS_FILE.read_text(encoding="utf-8").splitlines()]
        hosts = [ln for ln in lines if ln and not ln.startswith("#")]
        if hosts:
            return hosts
    return os.environ.get("CC_HOSTS", "").split()   # 沒設就是只看本機


def write_hosts(hosts):
    HOSTS_FILE.write_text(
        "# cc-center 要收集的機器, 一行一台 (# 開頭是註解)\n" + "\n".join(hosts) + "\n",
        encoding="utf-8")


def read_settings():
    s = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.is_file():
        try:
            s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return s


def write_settings(patch):
    s = read_settings()
    s.update({k: v for k, v in patch.items() if k in DEFAULT_SETTINGS})
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    return s


def tz_offset(tzname: str) -> str:
    """遠端可能沒 tzdata, 在本機先換成固定位移。"""
    try:
        off = datetime.now(scanner.parse_tz(tzname)).strftime("%z")
        return f"{off[:3]}:{off[3:]}"
    except Exception:
        return datetime.now().astimezone().strftime("%z")[:3] + ":00"
