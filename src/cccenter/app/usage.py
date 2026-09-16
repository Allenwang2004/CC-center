"""
方案用量: 5 小時跟 7 天的額度用了多少。

這兩個數字不在 transcript 裡, 只有 Claude Code 自己知道 —— 它每收到一則回覆就把
`rate_limits` 連同 context 用量塞給 statusline 指令。所以這裡做的是一個 statusline
的水槽: `cc-center-app statusline` 讀 stdin 的 JSON, 把要的欄位寫進
`~/.cc-center/usage.json`, 然後 (如果你原本就有 statusline) 把同一份 JSON 原封不動
餵給它, 輸出照樣印出去。介面那邊讀這個檔案, 從來不自己連 Anthropic。

    "statusLine": {"type": "command",
                   "command": "/path/to/bin/cc-center-app statusline -- python3 ~/.claude/my-statusline.py"}

檔案長這樣:

    {"at": 1789092423,                          # 最後一次收到的時間 (epoch 秒)
     "model": "Opus 5",
     "rate_limits": {"five_hour": {"used_percentage": 7, "resets_at": 1789100000},
                     "seven_day": {"used_percentage": 6, "resets_at": 1789500000}},
     "sessions": {"<session id>": {"used": 122201, "size": 1000000, "at": 1789092423}}}

`sessions` 是每個 session 最後一次報上來的 context, 拿來校正 scanner 對視窗大小的
猜測 (transcript 裡沒有那個數字)。七天沒動的就清掉。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .paths import STATE_DIR

USAGE_FILE = STATE_DIR / "usage.json"
KEEP_SESSIONS = 7 * 86400


def read():
    try:
        data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("at") else None


def record(payload: dict, at: float | None = None) -> dict:
    """把 statusline 的 JSON 併進快照。每個欄位都可能不在, 缺了就留上一次的。"""
    at = at if at is not None else time.time()
    snap = read() or {}
    snap["at"] = int(at)

    model = (payload.get("model") or {}).get("display_name")
    if model:
        snap["model"] = model

    limits = payload.get("rate_limits")
    if isinstance(limits, dict):
        kept = {}
        for name in ("five_hour", "seven_day"):
            w = limits.get(name)
            if isinstance(w, dict) and w.get("used_percentage") is not None:
                kept[name] = {"used_percentage": w["used_percentage"],
                              "resets_at": w.get("resets_at")}
        # 沒有這個欄位 (API key 用戶) 就別把舊的留著騙人; 有的話整個換掉,
        # 因為 Claude Code 在視窗重置後會把那一格拿掉。
        snap["rate_limits"] = kept

    sid = payload.get("session_id")
    ctx = payload.get("context_window") or {}
    if sid and ctx.get("context_window_size"):
        sessions = snap.setdefault("sessions", {})
        sessions[sid] = {"used": ctx.get("total_input_tokens") or 0,
                         "size": ctx["context_window_size"], "at": int(at)}
        for key in [k for k, v in sessions.items() if at - (v.get("at") or 0) > KEEP_SESSIONS]:
            del sessions[key]

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".usage-", dir=str(STATE_DIR))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)
    os.replace(tmp, USAGE_FILE)
    return snap


# -- 裝進 Claude Code 的設定

def claude_settings_file() -> Path:
    from .. import scanner
    return scanner.projects_dir().parent / "settings.json"


def hook_command() -> str:
    from .paths import FROZEN, ROOT
    if FROZEN:                                   # 桌面 app 的 sidecar 就是這個 binary
        return f"{sys.executable} statusline"
    return f"{ROOT / 'bin' / 'cc-center-app'} statusline"


def _load_settings(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise SystemExit(f"{path} 不是一個 JSON object, 不敢動它")
    return data


def cmd_statusline_install(_args=None):
    """把鉤子接到 Claude Code 的 statusLine 前面; 原本的指令保留, 接在 `--` 後面。"""
    path = claude_settings_file()
    data = _load_settings(path)
    current = data.get("statusLine") or {}
    old = (current.get("command") or "").strip() if current.get("type", "command") == "command" else ""
    hook = hook_command()
    if old.startswith(hook):
        print(f"本來就接好了: {path}\n  {old}")
        return 0
    command = f"{hook} -- {old}" if old else hook
    data["statusLine"] = {"type": "command", "command": command}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"接好了: {path}\n  {command}\n"
          "Claude Code 每收到一則回覆就會更新 ~/.cc-center/usage.json, Agents 分頁會跟著動。")
    return 0


def cmd_statusline_uninstall(_args=None):
    path = claude_settings_file()
    data = _load_settings(path)
    current = data.get("statusLine") or {}
    old = (current.get("command") or "").strip()
    hook = hook_command()
    if not old.startswith(hook):
        print("本來就沒接")
        return 0
    rest = old[len(hook):].strip()
    if rest.startswith("--"):
        rest = rest[2:].strip()
    if rest:
        data["statusLine"] = {"type": "command", "command": rest}
    else:
        data.pop("statusLine", None)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"拆掉了: {path}" + (f"\n  還原成 {rest}" if rest else "\n  statusLine 整個拿掉 (本來只有這個鉤子)"))
    return 0


def statusline_main(chain: list[str]) -> int:
    """`cc-center-app statusline [-- cmd ...]`: 記下來, 再交給原本的 statusline。

    這條路上什麼都不能炸 —— 一炸你的 status line 就空了。記錄失敗就默默略過,
    後面的指令照跑, 它的輸出跟 exit code 原樣傳回去。
    """
    raw = sys.stdin.buffer.read()
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
        if isinstance(payload, dict):
            record(payload)
    except Exception:                       # noqa: BLE001 - 見上面
        pass
    if not chain:
        return 0
    try:
        p = subprocess.run(chain, input=raw)
        return p.returncode
    except OSError as e:
        print(f"cc-center statusline: {e}", file=sys.stderr)
        return 0
