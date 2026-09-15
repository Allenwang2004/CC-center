#!/usr/bin/env python3
"""
The scanner: reads Claude Code's transcripts (and the repo's git log) and turns
them into facts. Nothing in here formats anything for a human -- that is what
`cccenter.render` is for -- and nothing in here remembers anything you wrote.

**This module is deliberately one self-contained file.** It is the only part of
cc-center that has to run on a machine where nothing is installed:

    ssh my-server "python3 - --json --host my-server --since … --until …" \
        < src/cccenter/scanner.py

The remote end reads it from stdin, so it cannot import a sibling module. Every
other module in this package may import `scanner`; `scanner` imports nothing but
the standard library. Keep it that way, or remote collection stops working on
the machines you cannot reach.

Run it directly and it behaves as that remote agent: window in, JSON out.
The full command line (merging, Markdown, reports) lives in `cccenter.cli`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------- 常數

FILE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Update"}
NOISE_TOOLS = {"Read", "Glob", "Grep", "TodoWrite", "LS", "ToolSearch"}
NOTABLE_CMDS = ("git", "npm", "pnpm", "yarn", "make", "pytest", "python", "python3",
                "cargo", "go", "docker", "uv", "pip", "kubectl", "terraform",
                "systemctl", "sbatch", "srun", "conda", "node", "bash")
# 這些 cmd 只是在看東西, 不算「做了什麼」
BORING_CMDS = ("ls", "cat", "cd", "pwd", "echo", "head", "tail", "grep", "find",
               "which", "wc", "sed", "awk", "sort", "uniq", "du", "df", "env")

# scratchpad / 系統暫存檔不算專案產出, 另外計數
_SCRATCH = re.compile(r"(^/private)?/tmp/|/scratchpad/|/\.cache/|/T/TemporaryItems/")

IDLE_GAP = 300          # 兩筆紀錄相隔超過這麼多秒就算離開座位, 不計入實際使用時間
TAIL_SECONDS = 30       # 最後一筆之後補算的秒數

GIT_ENABLED = True      # --no-git 可以關掉 (不去讀 repo, 就只有 commit 訊息沒有 sha)
GIT_SLACK = 240         # commit 比最後一筆動作晚這麼多秒以內, 仍然算在那一題頭上
GIT_TTL = 90            # 同一個 repo 的 git log 快取幾秒 (常駐介面會一直重掃)

TZ = None               # 由 --tz 設定; None = 本機時區
WEEKDAY = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

_GIT_COMMIT = re.compile(r"""git\s+commit\b[^\n]*?-m\s*(['"])(.+?)\1""", re.S)
_CMD_NAME = re.compile(r"<command-name>\s*(/[^<\s]+)")
_STRIP_TAGS = re.compile(
    r"<(system-reminder|local-command-stdout|local-command-stderr|command-message"
    r"|command-args|command-contents|user-prompt-submit-hook|ide_opened_file"
    r"|ide_selection|ide_diagnostics|user-memory-input)>.*?</\1>", re.S)
_IDE_FILE = re.compile(r"<ide_opened_file>.*?opened the file (\S+?) in the IDE")
_INTERRUPT = re.compile(r"^\[?Request interrupted|^\[Tool use was rejected")


# ---------------------------------------------------------------- 小工具

def projects_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"


def parse_tz(name):
    """吃 IANA 名稱 (Asia/Taipei) 或固定位移 (+08:00 / +0800)。
    遠端機器常常沒裝 tzdata, 所以 bin/cc-center-all 會直接餵位移過去。"""
    m = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", name or "")
    if m:
        sign = 1 if m.group(1) == "+" else -1
        return timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3))))
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception as e:
        print(f"  ! 時區 {name} 讀不到 ({e}), 改用本機時區", file=sys.stderr)
        return None


def local(dt: datetime) -> datetime:
    return dt.astimezone(TZ) if TZ else dt.astimezone()


def parse_ts(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def human_time(seconds) -> str:
    m = int(round((seconds or 0) / 60))
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h{m % 60:02d}m"


def human_num(n) -> str:
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.1f}k"
    return str(n)


def oneline(s, limit=None) -> str:
    s = " ".join(str(s or "").split())
    return s[:limit] + "…" if limit and len(s) > limit else s


def plural(n, one, many=None) -> str:
    """1 session / 2 sessions。數字四處都是, 冒出一個 "1 projects" 很難看。"""
    return f"{n} {one if n == 1 else (many or one + 's')}"


def rel(path, base):
    try:
        return str(Path(path).relative_to(base))
    except (ValueError, TypeError):
        return path


def text_of(content) -> str:
    """message.content 可能是 str 或 block list, 兩種都攤平成純文字。"""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts).strip()


def clean_prompt(raw: str) -> str:
    """把 hook 注入、system-reminder、貼上的檔案內容之類的雜訊拿掉。"""
    s = _STRIP_TAGS.sub(" ", raw or "")
    s = re.sub(r"<[^>\n]{1,40}>", " ", s)          # 殘留的孤兒 tag
    s = re.sub(r"Caveat:.*?</?[a-z-]+>", " ", s, flags=re.S)
    return oneline(s)


# ---------------------------------------------------------------- 還活著的 claude

def _etime_secs(raw):
    """ps 的 etime: [[dd-]hh:]mm:ss"""
    try:
        days = 0
        if "-" in raw:
            d, raw = raw.split("-", 1)
            days = int(d)
        parts = [int(x) for x in raw.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        return days * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]
    except (ValueError, IndexError):
        return None


def _cwd_of(pid):
    """Linux 直接讀 /proc, macOS 退回 lsof。"""
    try:
        return os.readlink("/proc/%s/cwd" % pid)
    except OSError:
        pass
    try:
        out = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=5).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def running_claude():
    """自己這個 uid 底下還活著的 claude。

    只認 argv[0] 的檔名就是 claude 的 (VS Code 那顆 native-binary 也算),
    `bash -c ... claude ...` 這種殼不算。transcript 只說對話停在哪,
    要知道「還有沒有東西能繼續它」就得看這個。
    """
    try:
        out = subprocess.run(["ps", "-Ao", "pid=,uid=,etime=,args="],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=15).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return []
    me = str(os.getuid())
    procs = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4 or not parts[0].isdigit() or parts[1] != me:
            continue
        pid, _, etime, args = parts
        exe = args.split(" ", 1)[0]
        if os.path.basename(exe) != "claude":
            continue
        up = _etime_secs(etime)
        # started 用「這台機器自己的時鐘」算, 才對得上同一台的 transcript 時間;
        # 拿收集端的 now() 去減遠端的 uptime 會被時鐘差扭掉。
        procs.append({"pid": int(pid), "cwd": _cwd_of(pid), "uptime": up,
                      "started": (time.time() - up) if up is not None else None,
                      "cmd": oneline(args, 120)})
    return procs


# ---------------------------------------------------------------- 判斷 human prompt

def user_turn_kind(rec):
    """('human', text, ide_file) / ('slash', '/name') / ('ide', path) / None。"""
    if rec.get("type") != "user":
        return None
    if rec.get("isMeta"):
        return None                                  # skill 注入、環境說明, 不是人打的
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return None

    raw = text_of(content)
    if not raw:
        return None

    slash = _CMD_NAME.search(raw)
    if slash:
        return ("slash", slash.group(1))

    origin = rec.get("origin") or {}
    if origin.get("kind") and origin.get("kind") != "human":
        return None                                  # task-notification 之類, 不是人打的
    if rec.get("promptSource") == "system":
        return None

    ide = _IDE_FILE.search(raw)
    # 舊版紀錄沒有 origin/promptSource, 只好靠清乾淨後還剩不剩字來判斷
    body = clean_prompt(raw)
    if not body or len(body) < 2 or _INTERRUPT.match(body):
        return ("ide", ide.group(1)) if ide else None
    return ("human", body, ide.group(1) if ide else None)


# ---------------------------------------------------------------- 掃描

def new_day():
    return {
        "start": None, "end": None, "active": 0.0, "_last": None,
        "assistant_turns": 0, "prompts": [], "slash": Counter(),
        "files": Counter(), "added": 0, "removed": 0,
        "scratch": Counter(),
        "commands": [], "commits": [], "tools": Counter(), "subagents": Counter(),
        "ide_files": Counter(), "artifacts": [],
        "turns": [],
        "tok_in": 0, "tok_out": 0, "cache_read": 0, "cache_write": 0,
        "errors": 0,
    }


def new_turn(kind, text, ts):
    """一題 = 一則提問, 加上它問完之後 agent 做的所有事, 直到你下一次開口。

    commands 存的是指令原文 (heredoc 幾行就幾行), 不在這裡壓成一行 —— 報告跟
    介面要一行版自己 `oneline`; journal 要的是完整的那份。said 是 agent 回你的話,
    「為什麼這樣改」多半在這裡, 不在指令裡。
    """
    return {"kind": kind, "text": text, "ts": ts, "end": ts,
            "files": {}, "commands": [], "commit_msgs": [], "said": [],
            "tools": Counter(), "subagents": [], "commits": []}


def patch_stats(tur):
    """從 toolUseResult 算 +/- 行數。"""
    added = removed = 0
    if not isinstance(tur, dict):
        return 0, 0
    for hunk in tur.get("structuredPatch") or []:
        for ln in (hunk or {}).get("lines") or []:
            if ln.startswith("+"):
                added += 1
            elif ln.startswith("-"):
                removed += 1
    if not added and not removed and tur.get("type") == "create":
        added = len((tur.get("content") or "").splitlines())
    return added, removed


def scan_session(path: Path, host: str):
    s = {
        "host": host, "file": str(path), "session_id": path.stem,
        "cwd": None, "branch": None, "version": None, "entrypoint": None,
        "sidechain": False, "ai_title": None, "summary": None, "last_prompt": None,
        "models": Counter(), "cost_usd": None, "lines_added_total": None,
        "tail": None,          # 最後停在哪 (在等你? 卡在工具? 還在跑?)
        "ctx": None,           # 最後一次 API 呼叫時 context 有多滿
        "days": {},
    }
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    # tool_use id -> file_path，好把 toolUseResult 的 diff 接回正確的檔案
    pending = {}
    day_of_uuid = {}
    # tool_use id -> (工具名, 發出的時間); 收到 tool_result 就拿掉。
    # 留在裡面的就是「發出去還沒回來」—— 不是還在跑, 就是在等你按權限。
    open_tools = {}
    # tool_use id -> 是哪一題發出來的, 好把行數與 commit 算回那一題頭上
    turn_of = {}
    # 一則回覆有幾個 content block 就寫幾筆 assistant 紀錄, usage 每筆都一樣 ——
    # token 只能算一次, 不然全部都是兩倍
    seen_responses = set()
    seen_said = set()       # (回覆 id, 文字) —— /fork、--continue 複製過去的紀錄不重複算
    cur = None              # 現在在回答的那一題
    tail = {"kind": None, "ts": None, "interrupted": False}

    def bucket(ts):
        key = local(ts).date().isoformat()
        d = s["days"].get(key)
        if d is None:
            d = s["days"][key] = new_day()
        if d["start"] is None or ts < d["start"]:
            d["start"] = ts
        if d["end"] is None or ts > d["end"]:
            d["end"] = ts
        if d["_last"] is not None:
            gap = (ts - d["_last"]).total_seconds()
            if 0 < gap <= IDLE_GAP:
                d["active"] += gap
        d["_last"] = max(ts, d["_last"]) if d["_last"] else ts
        return d

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue

        rtype = rec.get("type")

        # --- 沒有時間戳的 metadata 紀錄
        if rtype == "ai-title":
            s["ai_title"] = rec.get("aiTitle") or s["ai_title"]
            continue
        if rtype == "summary":
            s["summary"] = rec.get("summary") or s["summary"]
            continue
        if rtype == "last-prompt":
            s["last_prompt"] = rec.get("lastPrompt") or s["last_prompt"]
            continue
        if rtype == "cost-state":
            s["cost_usd"] = rec.get("totalCostUSD")
            s["lines_added_total"] = rec.get("totalLinesAdded")
            continue
        if rtype == "frame-link":
            url = rec.get("frameUrl")
            fts = parse_ts(rec.get("timestamp"))
            if url and fts:
                bucket(fts)["artifacts"].append({"title": rec.get("title"), "url": url})
            continue

        s["cwd"] = rec.get("cwd") or s["cwd"]
        s["branch"] = rec.get("gitBranch") or s["branch"]
        s["version"] = rec.get("version") or s["version"]
        s["entrypoint"] = rec.get("entrypoint") or s["entrypoint"]
        if rec.get("isSidechain"):
            s["sidechain"] = True

        ts = parse_ts(rec.get("timestamp"))
        if ts is None:
            # toolUseResult 有時掛在沒 timestamp 的 user 紀錄上, 用 parent 的日子
            if rtype == "user" and rec.get("toolUseResult") is not None:
                pass
            else:
                continue

        d = bucket(ts) if ts else s["days"].get(day_of_uuid.get(rec.get("parentUuid")))
        if d is None:
            continue
        if rec.get("uuid") and ts:
            day_of_uuid[rec["uuid"]] = local(ts).date().isoformat()
        if cur is not None and ts and (cur["end"] is None or ts > cur["end"]):
            cur["end"] = ts

        if ts and rtype in ("user", "assistant"):
            tail["kind"], tail["ts"] = rtype, ts
            tail["interrupted"] = bool(
                rtype == "user" and _INTERRUPT.search(text_of((rec.get("message") or {}).get("content"))))

        # 工具回來了就把它從 open_tools 拿掉 (tool_result 掛在 user 紀錄上)
        if rtype == "user":
            for b in (rec.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("tool_use_id"):
                    open_tools.pop(b["tool_use_id"], None)

        # --- tool_result 回填: 算 diff 行數 / 抓失敗
        tur = rec.get("toolUseResult")
        if tur is not None:
            add, rem = patch_stats(tur)
            tid = None
            for b in (rec.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("tool_use_id"):
                    tid = b["tool_use_id"]
                    break
            target = (tur.get("filePath") if isinstance(tur, dict) else None) or pending.get(tid)
            if not (target and _SCRATCH.search(target)):
                d["added"] += add
                d["removed"] += rem
                t = turn_of.get(tid)
                if t is not None and target and (add or rem):
                    ent = t["files"].setdefault(target, {"n": 0, "a": 0, "d": 0})
                    ent["a"] += add
                    ent["d"] += rem
            if isinstance(tur, dict) and tur.get("interrupted"):
                d["errors"] += 1
            elif isinstance(tur, str) and tur.startswith("Error"):
                d["errors"] += 1

        if rtype == "user":
            kind = user_turn_kind(rec)
            if not kind:
                continue
            if kind[0] == "human":
                d["prompts"].append(kind[1])
                cur = new_turn("human", kind[1], ts or tail["ts"])
                d["turns"].append(cur)
                if kind[2]:
                    d["ide_files"][kind[2]] += 1
            elif kind[0] == "slash":
                d["slash"][kind[1]] += 1
                cur = new_turn("slash", kind[1], ts or tail["ts"])
                d["turns"].append(cur)
            elif kind[0] == "ide" and kind[1]:
                d["ide_files"][kind[1]] += 1
            continue

        if rtype != "assistant":
            continue

        msg = rec.get("message") or {}
        response = rec.get("requestId") or msg.get("id")
        first_block = response is None or response not in seen_responses
        seen_responses.add(response)
        if first_block:
            d["assistant_turns"] += 1
            if msg.get("model") and msg["model"] != "<synthetic>":
                s["models"][msg["model"]] += 1
            u = msg.get("usage") or {}
            d["tok_in"] += u.get("input_tokens") or 0
            d["tok_out"] += u.get("output_tokens") or 0
            d["cache_read"] += u.get("cache_read_input_tokens") or 0
            d["cache_write"] += u.get("cache_creation_input_tokens") or 0
            # 最後一次呼叫送進去的 input 就是現在 context 裡有多少東西 —— 跟 Claude
            # Code statusline 的算法一樣 (不含 output)。/compact 之後自然會掉下來。
            used = ((u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
                    + (u.get("cache_read_input_tokens") or 0))
            if used and msg.get("model") and msg["model"] != "<synthetic>":
                s["ctx"] = {"used": used, "size": context_window(msg["model"]),
                            "model": msg["model"], "at": iso(ts)}

        content = msg.get("content")
        synthetic = msg.get("model") == "<synthetic>"     # "No response requested." 之類, 不是它說的
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                said = (block.get("text") or "").strip()
                if said and cur is not None and not synthetic:
                    key = (response, said)
                    if key not in seen_said:
                        seen_said.add(key)
                        cur["said"].append(said)
                continue
            if block.get("type") != "tool_use":
                continue
            name = block.get("name", "?")
            inp = block.get("input") or {}
            d["tools"][name] += 1
            if cur is not None:
                cur["tools"][name] += 1
            if block.get("id"):
                open_tools[block["id"]] = {"name": name, "ts": ts or tail["ts"]}
                if cur is not None:
                    turn_of[block["id"]] = cur
            if name in FILE_TOOLS:
                fp = inp.get("file_path") or inp.get("notebook_path")
                if fp:
                    scratch = bool(_SCRATCH.search(fp))
                    d["scratch" if scratch else "files"][fp] += 1
                    pending[block.get("id")] = fp
                    if cur is not None and not scratch:
                        cur["files"].setdefault(fp, {"n": 0, "a": 0, "d": 0})["n"] += 1
            elif name == "Bash":
                cmd = (inp.get("command") or "").strip()
                if cmd:
                    d["commands"].append(cmd)
                    if cur is not None:
                        cur["commands"].append(cmd)
                    for _, msgtext in _GIT_COMMIT.findall(inp.get("command") or ""):
                        msg = oneline(msgtext, 100)
                        d["commits"].append(msg)
                        if cur is not None:
                            cur["commit_msgs"].append(msg)
            elif name in ("Task", "Agent"):
                key = inp.get("subagent_type") or inp.get("description") or "task"
                d["subagents"][key] += 1
                if cur is not None:
                    cur["subagents"].append(key)

    for d in s["days"].values():
        if d["_last"] is not None:
            d["active"] += TAIL_SECONDS
        d.pop("_last", None)

    s["tail"] = tail_state(tail, open_tools)
    return s if s["days"] else None


def tail_state(tail, open_tools):
    """這個 session 最後停在哪。

    tool    —— 工具發出去了還沒有結果: 可能還在跑, 也可能在等你按權限
    waiting —— assistant 講完話就沒下文了 (或你按了中斷), 在等你回話
    running —— 最後一筆是你的提問或工具結果, 輪到 assistant 動
    """
    if not tail["ts"]:
        return None
    if open_tools:
        oldest = min(open_tools.values(),
                     key=lambda t: t["ts"] or tail["ts"])
        return {"state": "tool", "tool": oldest["name"],
                "since": iso(oldest["ts"] or tail["ts"]), "at": iso(tail["ts"]),
                "pending": len(open_tools)}
    if tail["kind"] == "assistant" or tail["interrupted"]:
        return {"state": "waiting", "tool": None,
                "since": iso(tail["ts"]), "at": iso(tail["ts"]),
                "interrupted": tail["interrupted"]}
    return {"state": "running", "tool": None,
            "since": iso(tail["ts"]), "at": iso(tail["ts"])}


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def context_window(model) -> int:
    """這個模型的 context 有多大 (token)。

    transcript 裡沒有這個數字, 只能從模型名字推: Claude Code 對 4.6 之後的 opus /
    sonnet 跟整個 5 系列都開 1M, haiku 跟更早的是 200k。statusline 有報的話
    (app/usage.py) 會蓋掉這裡的猜測。
    """
    m = (model or "").lower()
    if "[1m]" in m:
        return 1_000_000
    if "haiku" in m:
        return 200_000
    for tag in ("opus-5", "sonnet-5", "fable", "mythos",
                "opus-4-6", "opus-4-7", "opus-4-8", "sonnet-4-6"):
        if tag in m:
            return 1_000_000
    return 200_000


def title_of(s) -> str:
    for cand in (s.get("ai_title"), s.get("summary")):
        if cand:
            return oneline(cand, 110)
    for d in sorted(s["days"].values(),
                    key=lambda x: x["start"] or datetime.max.replace(tzinfo=timezone.utc)):
        if d["prompts"]:
            return oneline(d["prompts"][0], 110)
    if s.get("last_prompt"):
        return oneline(s["last_prompt"], 110)
    return "(no prompt)"


# ---------------------------------------------------------------- 收集 / 過濾

def window_from_args(days_back, target_date, since, until):
    """回傳 (since_utc, until_utc, wanted_dates|None)。"""
    if since or until:
        lo = parse_ts(since) or datetime.min.replace(tzinfo=timezone.utc)
        hi = parse_ts(until) or datetime.max.replace(tzinfo=timezone.utc)
        return lo, hi, None
    if target_date:
        day = datetime.strptime(target_date, "%Y-%m-%d").date()
        wanted = {day}
    else:
        today = local(datetime.now(timezone.utc)).date()
        wanted = {today - timedelta(days=i) for i in range(max(1, days_back))}
    lo = datetime.combine(min(wanted), datetime.min.time(), TZ or None)
    hi = datetime.combine(max(wanted) + timedelta(days=1), datetime.min.time(), TZ or None)
    if lo.tzinfo is None:
        lo, hi = lo.astimezone(), hi.astimezone()
    return lo, hi, {d.isoformat() for d in wanted}


def window_for_days(days):
    """給定一串日期, 回傳 (since, until, wanted) —— journal 的「上次到現在」用這個。"""
    wanted = {d for d in days if d}
    if not wanted:
        return window_from_args(1, None, None, None)
    lo = datetime.combine(datetime.strptime(min(wanted), "%Y-%m-%d").date(),
                          datetime.min.time(), TZ or None)
    hi = datetime.combine(datetime.strptime(max(wanted), "%Y-%m-%d").date()
                          + timedelta(days=1), datetime.min.time(), TZ or None)
    if lo.tzinfo is None:
        lo, hi = lo.astimezone(), hi.astimezone()
    return lo, hi, set(wanted)


def _cwd_of_transcript(path, max_lines=60):
    """從 transcript 前幾行撈 cwd —— 目錄名是編碼過的路徑, 還原不回來 (底線跟斜線都變成 -)。"""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= max_lines:
                    break
                if '"cwd"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict) and rec.get("cwd"):
                    return rec["cwd"]
    except OSError:
        pass
    return None


def all_projects():
    """~/.claude/projects 底下的每一個專案, 不管這個時間視窗裡有沒有動過。"""
    root = projects_dir()
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        try:
            files = sorted(d.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
        except OSError:
            continue
        if not files:
            continue
        cwd = None
        for f in files[:4]:                    # 最新的幾個裡面總有一個看得到 cwd
            cwd = _cwd_of_transcript(f)
            if cwd:
                break
        try:
            last = files[0].stat().st_mtime
        except OSError:
            last = 0
        out.append({"dir": d.name, "cwd": cwd or d.name, "resolved": bool(cwd),
                    "sessions": len(files), "last_active": last,
                    "exists": bool(cwd) and Path(cwd).is_dir(),
                    "is_git": bool(cwd) and Path(cwd, ".git").exists()})
    out.sort(key=lambda x: -x["last_active"])
    return out


def keep_days(s, lo, hi, wanted):
    """只留下落在視窗內的 day slice。"""
    out = {}
    for key, d in s["days"].items():
        if wanted is not None:
            if key in wanted:
                out[key] = d
            continue
        if d["end"] and d["end"] >= lo and d["start"] and d["start"] < hi:
            out[key] = d
    s["days"] = out
    return bool(out)


def collect(host, lo, hi, wanted, include_sidechains, entrypoints, oneshot=False):
    root = projects_dir()
    if not root.is_dir():
        sys.exit(f"[{host}] 找不到 {root} —— 確認 Claude Code 有跑過, 或設定 CLAUDE_CONFIG_DIR。")

    mtime_floor = (lo - timedelta(days=1)).timestamp()
    out = []
    for jsonl in root.glob("*/*.jsonl"):
        try:
            if jsonl.stat().st_mtime < mtime_floor:
                continue                    # 早就沒動過的檔案直接跳過, 省掉大部分 IO
        except OSError:
            continue
        s = scan_session(jsonl, host)
        if not s:
            continue
        if s["sidechain"] and not include_sidechains:
            continue
        # `claude -p` / SDK 的一次性執行 —— 多半是拿這個工具的輸出回去請 claude
        # 寫日誌, 本身不是一段工作。算進去的話, 你每整理一次就多一筆「做過的事」。
        if not oneshot and (s["entrypoint"] or "").startswith("sdk"):
            continue
        if entrypoints and (s["entrypoint"] or "cli") not in entrypoints:
            continue
        if not keep_days(s, lo, hi, wanted):
            continue
        out.append(s)
    return out


# ---------------------------------------------------------------- git

_GIT_CACHE = {}


def git_commits(cwd, lo, hi):
    """讀那個 repo 真正的 git log。

    transcript 裡只看得到 `git commit -m "..."` 這行指令, 沒有 sha ——
    而且你自己在終端機 commit 的根本不會出現在 transcript 裡。所以 sha 一律
    從 repo 撈, transcript 那邊的訊息只拿來當對照用的線索。
    """
    if not GIT_ENABLED or not cwd or not Path(cwd, ".git").exists():
        return []
    key = (cwd, lo.isoformat(), hi.isoformat())
    hit = _GIT_CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < GIT_TTL:
        return hit[1]

    try:
        raw = subprocess.run(
            ["git", "-C", cwd, "log", "--all", "--no-merges", "--numstat",
             "--since=" + lo.astimezone(timezone.utc).isoformat(),
             "--until=" + hi.astimezone(timezone.utc).isoformat(),
             "--format=%x00%H%x1f%h%x1f%aI%x1f%an%x1f%s"],
            capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        raw = ""

    out = []
    for chunk in raw.split("\x00"):
        if not chunk.strip():
            continue
        head, _, rest = chunk.partition("\n")
        parts = head.split("\x1f")
        if len(parts) < 5:
            continue
        sha, short, when, author, subject = parts[:5]
        files, added, removed = [], 0, 0
        for ln in rest.splitlines():
            bits = ln.split("\t")
            if len(bits) != 3:
                continue
            if bits[0].isdigit():
                added += int(bits[0])
            if bits[1].isdigit():
                removed += int(bits[1])
            files.append(bits[2])
        out.append({"sha": sha, "short": short, "at": when, "_ts": parse_ts(when),
                    "author": author, "subject": subject,
                    "files": files, "added": added, "removed": removed})
    out.sort(key=lambda c: c["_ts"] or datetime.min.replace(tzinfo=timezone.utc))
    _GIT_CACHE[key] = (now, out)
    return out


_STATUS_CACHE = {}


def git_status(cwd):
    """工作區現在還沒 commit 的檔案 (repo 相對路徑 -> 狀態碼)。"""
    if not GIT_ENABLED or not cwd or not Path(cwd, ".git").exists():
        return {}
    hit = _STATUS_CACHE.get(cwd)
    now = time.time()
    if hit and now - hit[0] < GIT_TTL:
        return hit[1]
    out = {}
    try:
        raw = subprocess.run(["git", "-C", cwd, "status", "--porcelain"],
                             capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        raw = ""
    for ln in raw.splitlines():
        if len(ln) > 3:
            path = ln[3:].strip()
            if " -> " in path:                 # rename: 記新的那個
                path = path.split(" -> ", 1)[1]
            out[path.strip('"')] = ln[:2].strip()
    _STATUS_CACHE[cwd] = (now, out)
    return out


def _lite(c):
    return {k: c[k] for k in ("sha", "short", "at", "author", "subject",
                              "files", "added", "removed")}


def _same_msg(subject, msg) -> bool:
    """transcript 裡的訊息被截到 100 字, 多行的也被壓成一行, 所以只比前綴。"""
    a = " ".join((subject or "").split()).lower()
    b = " ".join((msg or "").split()).lower()
    if not a or not b:
        return False
    n = min(len(a), len(b), 40)
    return a[:n] == b[:n]


def link_commits(sessions, lo, hi):
    """把 repo 裡的 commit 掛到問出它的那一題底下。

    兩輪: 先用 commit 訊息對 (最準), 剩下的再看時間有沒有落在那一題的區間裡。
    都對不上的就是你自己在別的地方 commit 的, 留在專案層級當「其他 commit」。
    """
    by_cwd = defaultdict(list)
    for s in sessions:
        if s.get("cwd"):
            by_cwd[s["cwd"]].append(s)

    orphans = {}
    for cwd, group in by_cwd.items():
        commits = git_commits(cwd, lo - timedelta(minutes=10), hi + timedelta(minutes=10))
        if not commits:
            continue
        turns = [(s, t) for s in group for day in s["days"].values()
                 for t in day["turns"]]
        turns.sort(key=lambda p: p[1]["ts"] or datetime.max.replace(tzinfo=timezone.utc))
        used = set()

        for _, t in turns:
            for msg in t["commit_msgs"]:
                for c in commits:
                    if c["sha"] not in used and _same_msg(c["subject"], msg):
                        t["commits"].append(_lite(c))
                        used.add(c["sha"])
                        break

        # 第二輪的上界不能吃到下一題, 不然 commit 會掛錯人
        for i, (_, t) in enumerate(turns):
            lo_t = t["ts"]
            if not lo_t:
                continue
            hi_t = (t["end"] or lo_t) + timedelta(seconds=GIT_SLACK)
            nxt = turns[i + 1][1]["ts"] if i + 1 < len(turns) else None
            if nxt and nxt < hi_t:
                hi_t = nxt
            for c in commits:
                if c["sha"] in used or not c["_ts"]:
                    continue
                if lo_t <= c["_ts"] <= hi_t:
                    t["commits"].append(_lite(c))
                    used.add(c["sha"])

        rest = [_lite(c) for c in commits
                if c["sha"] not in used and c["_ts"] and lo <= c["_ts"] < hi]
        if rest:
            orphans[cwd] = rest
    return orphans


# ---------------------------------------------------------------- JSON 進出

def turn_json(t, text_cap=400, full=False):
    """一題的 JSON。預設是給介面跟報告的精簡版 (提問截短、指令最多 20 筆、不帶 said);
    full=True 是給 journal 的完整版 —— 提問原文、每一筆指令、agent 說的每一段話。"""
    return {
        "kind": t["kind"],
        "text": t["text"] if full else oneline(t["text"], text_cap),
        "ts": iso(t["ts"]), "end": iso(t["end"]),
        "files": t["files"],
        "commands": t["commands"] if full else t["commands"][:20],
        "commit_msgs": t["commit_msgs"],
        "commits": t["commits"],
        "tools": dict(t["tools"]),
        "subagents": t["subagents"],
        "said": (t.get("said") or []) if full else [],
    }


def turn_obj(d):
    t = dict(d)
    t["ts"] = parse_ts(d.get("ts"))
    t["end"] = parse_ts(d.get("end"))
    t["tools"] = Counter(d.get("tools") or {})
    for k in ("files",):
        t.setdefault(k, {})
    for k in ("commands", "commit_msgs", "commits", "subagents", "said"):
        t.setdefault(k, [])
    return t


def to_json(s, prompt_cap=25, cmd_cap=60, full=False):
    d = dict(s)
    d["models"] = dict(s["models"])
    d["days"] = {}
    for key, day in s["days"].items():
        dd = dict(day)
        dd["start"] = day["start"].astimezone(timezone.utc).isoformat() if day["start"] else None
        dd["end"] = day["end"].astimezone(timezone.utc).isoformat() if day["end"] else None
        dd["files"] = dict(day["files"])
        dd["scratch"] = dict(day["scratch"])
        dd["tools"] = dict(day["tools"])
        dd["subagents"] = dict(day["subagents"])
        dd["slash"] = dict(day["slash"])
        dd["ide_files"] = dict(day["ide_files"])
        dd["prompts"] = (list(day["prompts"]) if full
                         else [oneline(p, 400) for p in day["prompts"][:prompt_cap]])
        dd["prompt_count"] = len(day["prompts"])
        dd["commands"] = day["commands"] if full else day["commands"][:cmd_cap]
        dd["turns"] = [turn_json(t, full=full) for t in day.get("turns") or []]
        d["days"][key] = dd
    return d


def from_json(d):
    s = dict(d)
    s["models"] = Counter(d.get("models") or {})
    days = {}
    for key, dd in (d.get("days") or {}).items():
        day = new_day()
        day.pop("_last", None)
        day.update(dd)
        day["start"] = parse_ts(dd.get("start"))
        day["end"] = parse_ts(dd.get("end"))
        day["files"] = Counter(dd.get("files") or {})
        day["scratch"] = Counter(dd.get("scratch") or {})
        day["tools"] = Counter(dd.get("tools") or {})
        day["subagents"] = Counter(dd.get("subagents") or {})
        day["slash"] = Counter(dd.get("slash") or {})
        day["ide_files"] = Counter(dd.get("ide_files") or {})
        day.setdefault("commits", [])
        day.setdefault("artifacts", [])
        day["turns"] = [turn_obj(t) for t in dd.get("turns") or []]
        days[key] = day
    s["days"] = days
    return s


def load_merge(paths):
    sessions, seen, orphans = [], set(), {}
    for p in paths:
        try:
            raw = sys.stdin.read() if p == "-" else Path(p).read_text(encoding="utf-8")
        except OSError as e:
            print(f"  ! 讀不到 {p}: {e}", file=sys.stderr)
            continue
        if not raw.strip():
            continue
        try:
            blob = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"  ! {p} 不是合法 JSON: {e}", file=sys.stderr)
            continue
        if isinstance(blob, dict):
            for cwd, lst in (blob.get("repo_commits") or {}).items():
                orphans.setdefault(cwd, []).extend(lst)
        for d in (blob.get("sessions", []) if isinstance(blob, dict) else blob):
            key = (d.get("host"), d.get("session_id"))
            if key in seen:
                continue
            seen.add(key)
            if d.get("days"):
                sessions.append(from_json(d))
    for lst in orphans.values():
        seen_sha, uniq = set(), []
        for c in lst:
            if c.get("sha") in seen_sha:
                continue
            seen_sha.add(c.get("sha"))
            uniq.append(c)
        lst[:] = uniq
    return sessions, orphans


# ---------------------------------------------------------------- 命令列共用

def add_window_args(ap: argparse.ArgumentParser) -> None:
    """決定「看哪一段時間」的旗標。cli 跟遠端 agent 用的是同一組, 不會走鐘。"""
    ap.add_argument("--date", help="YYYY-MM-DD, 只看這一天")
    ap.add_argument("--days", type=int, default=1, help="往回幾天 (預設 1 = 今天)")
    ap.add_argument("--since", help="UTC ISO 起點 (給 --json 用, 免得遠端要有 tzdata)")
    ap.add_argument("--until", help="UTC ISO 終點")
    ap.add_argument("--tz", default=None,
                    help="用哪個時區分日, 例如 Asia/Taipei 或 +08:00 (遠端沒 tzdata 時用位移)")


def add_filter_args(ap: argparse.ArgumentParser) -> None:
    """決定「哪些 session 算數」的旗標。"""
    ap.add_argument("--host", default=None, help="這台機器的標籤 (預設 hostname)")
    ap.add_argument("--sidechains", action="store_true", help="連 subagent 的獨立 transcript 也算")
    ap.add_argument("--oneshot", action="store_true",
                    help="連 claude -p / SDK 那種一次性執行也算進來 (預設不算)")
    ap.add_argument("--entrypoint", action="append",
                    help="只看某個入口 (claude-vscode / cli), 可重複")
    ap.add_argument("--no-git", action="store_true",
                    help="不去讀 repo 的 git log (commit 就只剩訊息, 沒有 sha)")


def apply_globals(args) -> str:
    """把旗標套到模組層的設定上, 回傳這次要用的 host 標籤。"""
    global TZ, GIT_ENABLED
    if getattr(args, "tz", None):
        TZ = parse_tz(args.tz)
    GIT_ENABLED = not getattr(args, "no_git", False)
    return args.host or socket.gethostname().split(".")[0]


def scan(host, args):
    """掃一次本機。回傳 (sessions, orphans, lo, hi)。"""
    lo, hi, wanted = window_from_args(args.days, args.date, args.since, args.until)
    sessions = collect(host, lo, hi, wanted, args.sidechains,
                       set(args.entrypoint) if args.entrypoint else None,
                       getattr(args, "oneshot", False))
    return sessions, link_commits(sessions, lo, hi), lo, hi


# ---------------------------------------------------------------- 牌價與每日總量

# (模型名裡的字串, input $/M, output $/M, cache 讀取是 input 的幾倍)。由上往下第一個
# 對到的算數, 所以長的名字要排在短的前面。cache 寫入: 5 分鐘 1.25 倍, 1 小時 2 倍。
PRICES = [
    ("fable-5-1", 10.0, 50.0, 0.025), ("mythos-5-1", 10.0, 50.0, 0.025),
    ("fable", 10.0, 50.0, 0.1), ("mythos", 10.0, 50.0, 0.1),
    ("opus-4-1", 15.0, 75.0, 0.1), ("opus-4-2025", 15.0, 75.0, 0.1),
    ("opus", 5.0, 25.0, 0.1),                     # opus 5 / 4.8 / 4.7 / 4.6 / 4.5
    ("sonnet-5", 2.0, 10.0, 0.1),
    ("sonnet", 3.0, 15.0, 0.1),                   # 4.6 / 4.5 / 4 / 3.7
    ("haiku-4", 1.0, 5.0, 0.1),
    ("haiku-3-5", 0.8, 4.0, 0.1),
    ("haiku", 0.25, 1.25, 0.1),
]


def price_of(model):
    m = (model or "").lower()
    for tag, pin, pout, read in PRICES:
        if tag in m:
            return pin, pout, read
    return 5.0, 25.0, 0.1


def api_cost(model, u) -> float:
    """一次呼叫照 API 牌價要多少錢。訂閱制實際上沒付這個數, 但這是唯一能比較的尺。"""
    pin, pout, read = price_of(model)
    cc = u.get("cache_creation") or {}
    w1h = cc.get("ephemeral_1h_input_tokens") or 0
    w5m = cc.get("ephemeral_5m_input_tokens") or 0
    if not (w1h or w5m):                          # 舊紀錄沒有分, 當 5 分鐘的
        w5m = u.get("cache_creation_input_tokens") or 0
    return ((u.get("input_tokens") or 0) * pin
            + (u.get("output_tokens") or 0) * pout
            + (u.get("cache_read_input_tokens") or 0) * pin * read
            + w5m * pin * 1.25 + w1h * pin * 2.0) / 1e6


def _usage_rows(path: Path):
    """一個 transcript 裡每一次 API 回覆: (回覆 id, 時間, 模型, usage)。只看 assistant。"""
    rows = []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"assistant"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict) or rec.get("type") != "assistant":
                    continue
                msg = rec.get("message") or {}
                model = msg.get("model")
                if not model or model == "<synthetic>" or not msg.get("usage"):
                    continue
                rows.append((rec.get("requestId") or msg.get("id") or rec.get("uuid"),
                             rec.get("timestamp"), model, msg["usage"]))
    except OSError:
        pass
    return rows


def daily_totals(cache=None):
    """每一天用了多少 token、照牌價多少錢 —— 不看視窗, 有紀錄的每一天都算。

    subagent 跟 `claude -p` 的 transcript 一樣算, 花掉的就是花掉的。`cache` 是
    {path: (mtime, size, rows)}, 收集端跨輪留著, 沒改過的檔案就不用再讀。
    """
    root = projects_dir()
    seen = set()
    days = {}
    for jsonl in sorted(root.glob("*/*.jsonl")) if root.is_dir() else []:
        try:
            st = jsonl.stat()
        except OSError:
            continue
        key = str(jsonl)
        hit = cache.get(key) if cache is not None else None
        if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
            rows = hit[2]
        else:
            rows = _usage_rows(jsonl)
            if cache is not None:
                cache[key] = (st.st_mtime, st.st_size, rows)
        for rid, ts_raw, model, u in rows:
            if rid in seen:                       # /fork 或 --continue 會把舊紀錄複製一份
                continue
            seen.add(rid)
            ts = parse_ts(ts_raw)
            if ts is None:
                continue
            day = local(ts).date().isoformat()
            d = days.get(day)
            if d is None:
                d = days[day] = {"calls": 0, "in": 0, "out": 0, "cache_read": 0,
                                 "cache_write": 0, "cost": 0.0, "models": {}}
            d["calls"] += 1
            d["in"] += u.get("input_tokens") or 0
            d["out"] += u.get("output_tokens") or 0
            d["cache_read"] += u.get("cache_read_input_tokens") or 0
            d["cache_write"] += u.get("cache_creation_input_tokens") or 0
            cost = api_cost(model, u)
            d["cost"] += cost
            d["models"][model] = d["models"].get(model, 0.0) + cost
    if cache is not None:
        live = {str(p) for p in root.glob("*/*.jsonl")} if root.is_dir() else set()
        for key in [k for k in cache if k not in live]:
            del cache[key]
    for d in days.values():
        d["cost"] = round(d["cost"], 4)
        d["models"] = {m: round(c, 4) for m, c in d["models"].items()}
    return days


def read_usage():
    """statusline 鉤子留下的方案用量快照 (5 小時 / 7 天), 沒有就 None。

    這是唯一一個要看 ~/.cc-center 的地方 —— 因為額度是帳號層級的, 哪台機器報的
    都一樣, 遠端有裝鉤子的話也順便帶回來, 收集端挑最新的那份。
    """
    path = Path(os.environ.get("CC_CENTER_STATE", Path.home() / ".cc-center")) / "usage.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("at") else None


def json_report(sessions, orphans, lo, hi, projects=None, processes=None, full=False) -> dict:
    """收集端跟遠端 agent 講的是同一種話。full 見 turn_json。"""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "usage": read_usage(),
        "daily": daily_totals(),
        "window": {"since": lo.astimezone(timezone.utc).isoformat(),
                   "until": hi.astimezone(timezone.utc).isoformat()},
        "processes": processes if processes is not None else [],
        "repo_commits": orphans,
        "projects": projects if projects is not None else [],
        "sessions": [to_json(s, full=full) for s in sessions],
    }


def main(argv=None) -> int:
    """遠端 agent: 掃這台機器, 把事實印成 JSON。"""
    ap = argparse.ArgumentParser(
        prog="cccenter.scanner",
        description="掃這台機器的 Claude Code transcript, 輸出 JSON",
        epilog="這是遠端 agent。要 Markdown 報告請用 cc-center。")
    add_window_args(ap)
    add_filter_args(ap)
    ap.add_argument("--json", action="store_true",
                    help="輸出 JSON (這個模式只會輸出 JSON, 給不給都一樣)")
    ap.add_argument("--full", action="store_true",
                    help="每一題連提問原文、每一筆指令、agent 說的話都帶 (journal 用; 大很多)")
    args = ap.parse_args(argv)

    host = apply_globals(args)
    sessions, orphans, lo, hi = scan(host, args)
    print(json.dumps(json_report(sessions, orphans, lo, hi,
                                 projects=all_projects(), processes=running_claude(),
                                 full=args.full),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)   # 讓 | head 不噴 traceback
    except (ImportError, AttributeError, ValueError):
        pass
    sys.exit(main())
