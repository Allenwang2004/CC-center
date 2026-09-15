#!/usr/bin/env python3
"""
cc-center-app — the local interface, and the watcher behind it.

    bin/cc-center-app                  # start in the background (or just open it)
    bin/cc-center-app serve            # stay in the foreground, print the log
    bin/cc-center-app status | stop | restart | open | logs
    bin/cc-center-app install          # start on login (launchd / systemd)
    bin/cc-center-app uninstall
    bin/cc-center-app statusline-install   # hook Claude Code's statusLine so the
                                           # Agents tab can show the plan's 5h / 7d
                                           # usage; your existing command keeps running
    bin/cc-center-app statusline-uninstall
    bin/cc-center-app statusline [-- cmd ...]  # the hook itself (Claude Code runs this)
    bin/cc-center-app journal              # write yesterday's journal for every project
    bin/cc-center-app journal --date 2026-09-10 --project ~/x [--dry-run | --facts] [--force]

Once it is up it stays out of your way:
  - rescans this machine when a transcript actually changes (mtime, every 12s)
  - collects the other machines over ssh on a timer (5 min, adjustable or off)
  - pushes changes to the open page, so nothing needs refreshing
  - every morning, writes yesterday's journal for each project (claude -p, haiku)

Standard library only, no pip install. Binds 127.0.0.1 and nothing else, and
every /api call needs the token the page was served with. Scanning is the same
`cccenter.scanner` the command line uses, so the two can never disagree.
"""

from __future__ import annotations

import argparse
import os
import sys

from .daemon import cmd_logs, cmd_open, cmd_start, cmd_status, cmd_stop
from .journal import cmd_journal
from .server import serve
from .service import cmd_install, cmd_uninstall
from .settings import migrate_old_names, write_settings
from .usage import cmd_statusline_install, cmd_statusline_uninstall, statusline_main


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="cc-center-app",
        description="cc-center 的本機介面 + 常駐監看",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--port", type=int, default=int(os.environ.get("CC_CENTER_PORT", 8787)))
    ap.add_argument("--bind", default="127.0.0.1", help="預設只綁 localhost")
    ap.add_argument("--no-open", action="store_true", help="不要自動開瀏覽器")
    ap.add_argument("--browser", default=None,
                    help="用哪個瀏覽器開 (macOS 的 app 名稱, 例如 Arc; 空字串 = 系統預設)")
    ap.add_argument("-f", "--follow", action="store_true", help="logs: 跟著跑")
    j = ap.add_argument_group("journal", "手動產 journal (預設昨天, 所有專案、所有機器)")
    j.add_argument("--date", help="哪一天 (YYYY-MM-DD)")
    j.add_argument("--days", type=int, default=1, help="從昨天往回幾天 (預設 1)")
    j.add_argument("--project", help="只做這個專案 (路徑)")
    j.add_argument("--host", help="只做這台機器")
    j.add_argument("--force", action="store_true", help="已經有的也重產 (會蓋掉你補的字)")
    j.add_argument("--dry-run", action="store_true", help="印出來, 不存")
    j.add_argument("--facts", action="store_true", help="只印餵給 claude 的事實, 不問它")
    ap.add_argument("cmd", nargs="?", default="start",
                    choices=["start", "serve", "stop", "restart", "status", "open",
                             "logs", "install", "uninstall", "journal",
                             "statusline-install", "statusline-uninstall"])
    return ap


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # 這條是 Claude Code 每則回覆都會叫一次的鉤子, 不走 argparse (後面那串是別人的指令)
    if argv[:1] == ["statusline"]:
        rest = argv[1:]
        return statusline_main(rest[1:] if rest[:1] == ["--"] else rest)
    args = build_parser().parse_args(argv)
    migrate_old_names()
    if args.browser is not None:
        write_settings({"browser": args.browser})

    if args.cmd == "serve":
        return serve(args.port, args.bind, not args.no_open) or 0
    if args.cmd == "start":
        return cmd_start(args)
    if args.cmd == "restart":
        cmd_stop(args)
        return cmd_start(args)
    return {"stop": cmd_stop, "status": cmd_status, "open": cmd_open,
            "logs": cmd_logs, "install": cmd_install, "uninstall": cmd_uninstall,
            "journal": cmd_journal,
            "statusline-install": cmd_statusline_install,
            "statusline-uninstall": cmd_statusline_uninstall}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main() or 0)
