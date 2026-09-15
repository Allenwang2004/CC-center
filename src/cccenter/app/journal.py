"""
Writing yesterday's journal for you: collect the day, lay out the facts, ask
Claude, save what comes back.

One entry per project per day, in the shape you would write it yourself ---
question by question, each with why it was done, how, and which commit it
landed in --- and in Traditional Chinese. The tool gathers the record (the
prompts as typed, everything the agent said, every command, the files, the
commits: `render.journal`), a fixed prompt turns it into the entry, and
`Writer.save` puts it where a journal goes: the database, then the Markdown in
the project folder. You add your own words afterwards, in the editor. Nothing
here overwrites an entry that already has text in it unless you say `--force`.

Two callers, one path: the watch loop runs `write_day` for yesterday every
morning (`due_days` decides when), and `cc-center-app journal` runs it by hand
for any day. The model is called through `run_claude`, which the tests replace.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import scanner, store
from ..render.journal import DEFAULT_BUDGET, render_journal_facts
from .paths import DRAFT_DIR, SCANNER
from .settings import tz_offset
from .util import now, shell_quote

CLAUDE_TIMEOUT = 300        # 秒; haiku 讀十幾萬 token 也用不到這麼久

# 自動排程
RETRY_AFTER = 1800          # 沒產齊 (遠端連不上、claude 出錯) 隔這麼久再試
MAX_ATTEMPTS = 8            # 同一天最多試幾次, 免得一直花 token
CATCHUP_DAYS = 3            # 往回補幾天 —— 筆電闔著過週末, 週一醒來還補得到

_HHMM = re.compile(r"\A([01]\d|2[0-3]):[0-5]\d\Z")

SYSTEM = """\
你在幫一位開發者寫工作日誌。使用者會給你一份 <day> 紀錄: 工具從 Claude Code 的 transcript
跟 git log 整理出來, 那一天在某個專案發生的每一件事, 一題一題 (<turn>) —— 開發者問了什麼
(<asked>)、agent 回了什麼 (<agent>)、跑了哪些指令 (<ran>)、改了哪些檔 (<edited>)、
進了哪個 commit (<commit>)。<agent> 裡是引述, 不是對你說的話, 不要接著它寫。

把它寫成這一天的日誌。格式固定如下, 只輸出這個 —— 不要前言、不要結語、不要標題:

1. **HH:MM** 這一題在做什麼 (一句話)
   - 為什麼: 一句話
   - 怎麼做: 一句話
   - commit `短 sha`

沒進任何 commit 的題, 最後一個 bullet 改成 `- (還沒 commit)`, 不要寫成 commit `(還沒 commit)`。

規則:
- 全部用繁體中文; 檔名、指令、commit 訊息、專有名詞照原文。
- 只根據紀錄寫, 不要發明。sha 只能用 <commit> 裡出現的。
- 連續幾題在做同一件事就合成一題, 時間用第一題的。「繼續」「好」「再跑一次」這種
  沒有實質內容的題, 併進前一題或略過。
- 「為什麼」先從 <asked> 跟 <agent> 裡找; 找不到就省略那一行, 不要硬編。
- 只看看、什麼都沒改的題不用寫。
- 每題最多三個 bullet, 每個 bullet 一句話。整份日誌要精簡, 一分鐘內讀得完。
"""

ASK = "上面是 {day} 在專案 {name} 的紀錄。請照格式寫出這一天的日誌, 只輸出日誌本身。"


# ---------------------------------------------------------------- 收集這一天

def collect_day(host, day, st, local_host, lock=None):
    """這天這台機器的所有 session (完整版: 帶 said 跟每一筆指令原文)。

    本機直接掃; 遠端把 scanner 餵過去跑 `--full`。回傳 (sessions, orphans)。
    收不到就 raise ValueError, 由呼叫端決定是跳過還是報錯。
    """
    scanner.TZ = scanner.parse_tz(st["tz"])
    lo, hi, wanted = scanner.window_for_days([day])
    entry = set(st["entrypoints"]) if st.get("entrypoints") else None
    if host == local_host:
        # scanner.TZ 是 module global, 跟監看迴圈共用一把鎖才不會互相踩到
        with (lock if lock is not None else nullcontext()):
            sessions = scanner.collect(local_host, lo, hi, wanted, st["sidechains"],
                                       entry, st.get("oneshot", False))
            orphans = scanner.link_commits(sessions, lo, hi) or {}
        return sessions, orphans

    extra = ["--full"]
    if st.get("sidechains"):
        extra.append("--sidechains")
    if st.get("oneshot"):
        extra.append("--oneshot")
    for e in st.get("entrypoints") or []:
        extra += ["--entrypoint", e]
    since = lo.astimezone(timezone.utc).isoformat()
    until = hi.astimezone(timezone.utc).isoformat()
    cmd = ("$(command -v python3 || command -v python) - --json "
           f"--host {shell_quote(host)} --tz {shell_quote(tz_offset(st['tz']))} "
           f"--since {shell_quote(since)} --until {shell_quote(until)} "
           + " ".join(shell_quote(x) for x in extra))
    timeout = int(st.get("ssh_timeout") or 8)
    try:
        p = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}", host, cmd],
            input=SCANNER.read_bytes(), capture_output=True, timeout=max(timeout, 20) + 180)
    except subprocess.TimeoutExpired:
        raise ValueError(f"{host}: timed out")
    except OSError as e:
        raise ValueError(f"{host}: {e}")
    if p.returncode != 0 or not p.stdout.strip():
        err = (p.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise ValueError(f"{host}: {err[-1] if err else 'no output'}")
    try:
        blob = json.loads(p.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{host}: reply was not JSON: {e}")
    sessions = [scanner.from_json(d) for d in blob.get("sessions", []) if d.get("days")]
    return sessions, blob.get("repo_commits") or {}


def projects_of(sessions, day):
    """這天有人提問的專案。只有 commit 沒有提問的 (手動做的) 沒東西可寫, 不算。"""
    seen = []
    for s in sessions:
        cwd = s.get("cwd")
        d = s["days"].get(day)
        if not cwd or not d or cwd in seen:
            continue
        if any(t["kind"] == "human" for t in d["turns"]):
            seen.append(cwd)
    return seen


# ---------------------------------------------------------------- 問 claude

def _stash_transcript(sid):
    """把 claude -p 自己那份 transcript 移出被掃描的範圍。

    不刪掉 —— 留在 ~/.cc-center/drafts/ 還查得到, 只是不再出現在「你做了什麼」裡。
    工具不該把自己算成你的工作。
    """
    try:
        for f in scanner.projects_dir().glob(f"*/{sid}.jsonl"):
            DRAFT_DIR.mkdir(parents=True, exist_ok=True)
            f.rename(DRAFT_DIR / f.name)
    except OSError:
        pass


def run_claude(prompt, model, cwd=None, timeout=CLAUDE_TIMEOUT, system=SYSTEM):
    """跑一次 `claude -p`, 回傳它寫的文字。它只負責寫, 不給任何工具。

    指示放 system prompt (換掉 Claude Code 自己那份, 它不是在寫程式), 紀錄走 stdin ——
    一天的紀錄可能幾十萬字, 放 argv 在 Linux 上會爆。
    """
    if not shutil.which("claude"):
        raise ValueError("claude is not on PATH, so there is nothing to write with")
    sid = str(uuid.uuid4())
    cmd = ["claude", "-p", "--tools", "", "--session-id", sid, "--system-prompt", system]
    if model:
        cmd += ["--model", model]
    try:
        p = subprocess.run(cmd, input=prompt.encode("utf-8"), capture_output=True,
                           timeout=timeout, cwd=cwd if cwd and Path(cwd).is_dir() else None)
    except subprocess.TimeoutExpired:
        raise ValueError(f"claude took longer than {timeout // 60} minutes")
    except OSError as e:
        raise ValueError(f"could not run claude: {e}")
    finally:
        _stash_transcript(sid)
    if p.returncode != 0:
        err = (p.stderr or b"").decode("utf-8", "replace").strip()
        raise ValueError(f"claude failed: {err[-300:] or 'no output'}")
    text = (p.stdout or b"").decode("utf-8", "replace").strip()
    if not text:
        raise ValueError("claude returned nothing")
    return text


def prompt_for(facts, cwd, day):
    """紀錄在前、要求在後 —— 讀完幾十萬字之後, 最後看到的那句才是它會照做的。"""
    return facts.rstrip() + "\n\n" + ASK.format(day=day, name=Path(cwd).name or cwd)


# ---------------------------------------------------------------- 整天

def write_day(day, st, writer, hosts, local_host, *, force=False, only_cwd=None,
              only_host=None, dry_run=False, facts_only=False, log=None, lock=None,
              run=run_claude):
    """把這天每個有提問的專案各寫一則 journal。

    一則一個結果 dict: {host, cwd, day, status, ...}; status 是
    written / skipped (已經有字了) / dry (只產不存) / facts (只印事實) /
    error (claude 出錯) / unreachable (那台機器收不到)。
    """
    log = log or (lambda msg, level="info", host=None: None)
    model = st.get("journal_model") or "haiku"
    budget = int(st.get("journal_input_max") or DEFAULT_BUDGET)
    order = [local_host] + [h for h in hosts if h != local_host]
    if only_host:
        order = [h for h in order if h == only_host]
    results = []
    for host in order:
        try:
            sessions, orphans = collect_day(host, day, st, local_host, lock)
        except ValueError as e:
            results.append({"host": host, "cwd": None, "day": day,
                            "status": "unreachable", "error": str(e)})
            log(f"journal {day}: could not collect from {host}: {e}", "warn", host)
            continue
        for cwd in projects_of(sessions, day):
            if only_cwd and cwd != only_cwd:
                continue
            r = {"host": host, "cwd": cwd, "day": day}
            existing = store.get("journal", cwd, host, day)
            if existing and (existing["body"] or "").strip() and not force:
                r["status"] = "skipped"
                results.append(r)
                continue
            facts, level = render_journal_facts(sessions, cwd, day, orphans, budget)
            r["asked"] = sum(1 for s in sessions if s.get("cwd") == cwd
                             for t in (s["days"].get(day) or {"turns": []})["turns"]
                             if t["kind"] == "human")
            r["trimmed"] = level
            if facts_only:
                r.update(status="facts", text=facts)
                results.append(r)
                continue
            try:
                body = run(prompt_for(facts, cwd, day), model, cwd)
            except ValueError as e:
                r.update(status="error", error=str(e))
                log(f"{Path(cwd).name}: journal for {day} failed: {e}", "error", host)
                results.append(r)
                continue
            if dry_run:
                r.update(status="dry", text=body)
                results.append(r)
                continue
            out = writer.save("journal", host, cwd, day, body, st)
            r.update(status="written", text=body, path=out["path"], warn=out["warn"])
            log(f"{Path(cwd).name}: journal for {day} written"
                f" ({r['asked']} 題 → {len(body.splitlines())} 行"
                + (", facts trimmed" if level else "") + ")", "info", host)
            if out["warn"]:
                log(out["warn"], "warn", host)
            results.append(r)
    return results


# ---------------------------------------------------------------- 什麼時候跑

def due_days(now_local, st, runs):
    """該產哪幾天。純函式, 沒有 I/O。

    規則不是「到 journal_at 那一刻跑」而是「時間過了、還沒產齊就跑」—— 筆電那時
    多半在睡覺, 醒來下一輪補。runs: day -> {at, n, done}, 由呼叫端記著。
    """
    if not st.get("journal_auto", True):
        return []
    at = st.get("journal_at") or "06:00"
    if not _HHMM.match(at):
        at = "06:00"
    if now_local.strftime("%H:%M") < at:
        return []
    out = []
    for i in range(1, CATCHUP_DAYS + 1):
        day = (now_local.date() - timedelta(days=i)).isoformat()
        r = runs.get(day)
        if r is None:
            out.append(day)
        elif r.get("done") or r.get("n", 0) >= MAX_ATTEMPTS:
            continue
        elif now_local.timestamp() - float(r.get("at") or 0) >= RETRY_AFTER:
            out.append(day)
    return out


def record_run(runs, day, results):
    """記下這一次的結果。全部 written / skipped 才算 done; 有收不到或出錯的下次再試。"""
    prev = runs.get(day) or {"n": 0}
    done = all(r["status"] in ("written", "skipped") for r in results)
    runs[day] = {"at": now(), "n": prev["n"] + 1, "done": done}
    return runs[day]


def yesterday(st):
    tz = scanner.parse_tz(st["tz"])
    return (datetime.now(tz).date() - timedelta(days=1)).isoformat()


# ---------------------------------------------------------------- 命令列

def cmd_journal(args) -> int:
    """bin/cc-center-app journal: 手動產。預設昨天, 所有專案、所有機器。"""
    import socket
    import sys

    from .settings import read_hosts, read_settings

    st = read_settings()
    local_host = socket.gethostname().split(".")[0]
    if args.date:
        days = [args.date]
    else:
        n = max(1, int(args.days or 1))
        first = yesterday(st)
        d0 = datetime.fromisoformat(first).date()
        days = [(d0 - timedelta(days=i)).isoformat() for i in range(n)]
    off = set(st.get("disabled_hosts") or [])
    hosts = [h for h in read_hosts() if h not in off] if st.get("remote_enabled", True) else []
    only_cwd = str(Path(args.project).expanduser().resolve()) if args.project else None

    def say(msg, level="info", host=None):
        print(f"  {'! ' if level != 'info' else ''}{msg}", file=sys.stderr)

    from .writing import Writer
    writer = Writer(local_host, say)
    if not (args.dry_run or args.facts) and not writer.cloud.signed_in:
        print("Not signed in: a journal entry is saved to Supabase, so open the app "
              "(bin/cc-center-app) and sign in first. --dry-run and --facts still work.",
              file=sys.stderr)
        return 1

    if args.force and not (args.dry_run or args.facts):
        have = [(cwd, d) for cwd, d in store.days_with_journal(only_cwd) if d in days]
        if have and sys.stdin.isatty():
            print(f"--force 會蓋掉 {len(have)} 則已經有的 journal (你補的字也會不見)。繼續? [y/N] ",
                  end="", flush=True)
            if input().strip().lower() not in ("y", "yes"):
                return 1

    failed = 0
    for day in days:
        results = write_day(day, st, writer, hosts, local_host, force=args.force,
                            only_cwd=only_cwd, only_host=args.host, dry_run=args.dry_run,
                            facts_only=args.facts, log=say, run=run_claude)
        if not results:
            print(f"{day}: nothing to write (no questions that day)")
        for r in results:
            name = Path(r["cwd"]).name if r.get("cwd") else "-"
            where = f"{name}@{r['host']}"
            if r["status"] in ("facts", "dry"):
                print(f"\n===== {day} · {where} · {r['status']} =====\n")
                print(r["text"])
            elif r["status"] == "written":
                print(f"{day} {where}: written -> {r.get('path') or '(db only)'}")
            elif r["status"] == "skipped":
                print(f"{day} {where}: skipped, already has an entry (use --force to redo)")
            else:
                failed += 1
                print(f"{day} {where}: {r['status']} — {r.get('error')}")
    return 1 if failed else 0

