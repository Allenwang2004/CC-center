"""
Reading `.env` at the repository root, so that the machine list, the time zone
and the rest of what is personal to you stay out of the code.

Standard library only, and deliberately tiny: `KEY=value` lines, `#` comments,
an optional `export`, and single or double quotes around a value. A variable
that is already set in the environment always wins --- `.env` only fills in
what is missing, so `CC_HOSTS="a b" bin/cc-center-all` still overrides it.

Both the command line and the app call `load_env()` before reading any `CC_*`
variable; `bin/cc-center-all` reads the same file with the same rules.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"   # repo 根目錄

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse_env(text: str) -> dict[str, str]:
    """`.env` 的內容 → dict。不合格式的行直接跳過, 不噴錯。"""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key] = val
    return out


def load_env(path: Path = ENV_FILE) -> dict[str, str]:
    """把 `.env` 裡還沒設的變數補進 os.environ, 回傳這次真的補上的那些。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    added = {}
    for key, val in parse_env(text).items():
        if key not in os.environ:
            os.environ[key] = val
            added[key] = val
    return added
