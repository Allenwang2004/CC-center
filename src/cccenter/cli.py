#!/usr/bin/env python3
"""
The command line: one day, many days, one machine or all of them.

    cc-center                        # today
    cc-center --days 7               # the last seven days
    cc-center --date 2026-09-05
    cc-center --by-project           # grouped by project, question by question
    cc-center --json                 # the facts, for something else to read

Many machines (nothing is installed on the far end --- the scanner is piped in
over ssh; `bin/cc-center-all` does the whole dance):

    ssh my-server "python3 - --json --host my-server --since … --until …" \
        < src/cccenter/scanner.py > /tmp/my-server.json
    cc-center --merge /tmp/*.json --tz Asia/Taipei

Hand it to Claude to write up:

    bin/cc-center-all --json | claude -p "根據這份 JSON 寫一篇中文工作日誌"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import render, scanner
from .env import load_env


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="cc-center",
        description="整理 Claude Code 每天做了什麼 (支援多機合併)",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    scanner.add_window_args(ap)
    scanner.add_filter_args(ap)
    ap.add_argument("--json", action="store_true", help="輸出 JSON 而非 Markdown")
    ap.add_argument("--by-project", action="store_true",
                    help="按專案排, 一題一題列出 提問 → 改動 → commit")
    ap.add_argument("--tokens", action="store_true", help="每個 session 多印 token/花費")
    ap.add_argument("--prompts", type=int, default=5, help="每個 session 最多列幾則提問")
    ap.add_argument("--merge", nargs="+", metavar="FILE",
                    help="合併多個 --json 產出 ('-' 代表 stdin), 不掃本機")
    ap.add_argument("-o", "--out", help="寫到檔案而不是 stdout")
    return ap


def main(argv=None) -> int:
    load_env()                                   # repo 根目錄的 .env, 環境變數優先
    args = build_parser().parse_args(argv)
    host = scanner.apply_globals(args)

    if args.merge:
        sessions, orphans = scanner.load_merge(args.merge)
        lo, hi, wanted = scanner.window_from_args(args.days, args.date,
                                                  args.since, args.until)
        if args.date or args.since or args.until or args.days != 1:
            sessions = [s for s in sessions if scanner.keep_days(s, lo, hi, wanted)]
        multi_host = len({s["host"] for s in sessions}) > 1
        projects, processes = [], []
    else:
        sessions, orphans, lo, hi = scanner.scan(host, args)
        multi_host = bool(args.host)
        projects, processes = scanner.all_projects(), scanner.running_claude()

    if args.json:
        text = json.dumps(
            scanner.json_report(sessions, orphans, lo, hi, projects, processes),
            ensure_ascii=False, indent=2)
    elif args.by_project:
        text = render.render_projects(sessions, orphans)
    else:
        text = render.render(sessions, multi_host, args.prompts, args.tokens)

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"寫到 {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)   # 讓 | head 不噴 traceback
    except (ImportError, AttributeError, ValueError):
        pass
    sys.exit(main())
