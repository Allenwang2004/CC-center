"""
Saving what you wrote --- and what was written for you.

Everything here is about the half of cc-center that cannot be recomputed. The
truth is Supabase (`cccenter.cloud`); the SQLite file is the cache of it on this
machine (`cccenter.store`); a note also lands as Markdown in the project folder
(`cccenter.entries`). A save goes cloud first: if Supabase does not have it,
nothing else does, and you get told. If only the Markdown copy fails --- the
folder is gone, the machine is unreachable --- what you wrote is still saved.

A journal entry stays out of the project folder. It is written by a machine
every morning and belongs to the account, not to the repo.

The journal Claude writes every morning (`app/journal.py`) comes through the same
`save`, so there is exactly one way an entry reaches the cloud and the cache.

Deliberately knows nothing about the watcher: hand it a hostname, a cloud and
somewhere to log, and it can be used from a test, or from a script, without a
server running.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from .. import cloud as cloudmod
from .. import entries, store, sync
from .util import shell_quote


class Writer:
    """journal / note 的存檔與匯出。

    log(msg, level, host) 是選填的; 沒給就靜靜地做完。
    cloud 沒給就用環境變數那顆 (SUPABASE_URL / SUPABASE_ANON_KEY)。
    """

    def __init__(self, local_host, log=None, cloud=None):
        self.local_host = local_host
        self.log = log if log is not None else (lambda msg, level="info", host=None: None)
        self.cloud = cloud if cloud is not None else cloudmod.default()
        self.adopted: set = set()          # 已經把既有 markdown 收進快取的專案

    def adopt(self, cwds, host):
        """第一次看到某個專案時, 把它資料夾裡既有的 journal/note markdown 收進快取。

        收進來的先標成「雲端還沒有」, 下一次 sync 推上去。之後就只認雲端 ——
        檔案是投影, 不再回頭 parse。回傳這次收了幾則。
        """
        total = 0
        for cwd in cwds:
            if cwd in self.adopted:
                continue
            self.adopted.add(cwd)
            try:
                n = store.import_markdown(cwd, host)
            except (OSError, ValueError, sqlite3.Error) as e:      # noqa: BLE001
                self.log(f"{Path(cwd).name}: could not import existing markdown: {e}", "error", host)
                continue
            if n:
                total += n
                self.log(f"{Path(cwd).name}: imported {n} existing journal/note files", "info", host)
        return total

    def save(self, kind, host, cwd, ident, text, st, title=None):
        """存一則 journal / note。

        雲端是真相: 先寫上去, 成功了才進快取; 然後 note 再投影成專案裡的 markdown
        (讓它跟著 git 走)。投影失敗 (遠端連不上、資料夾不見了) 不影響已經存好的
        內容, 只回報一聲。雲端寫不上去就整個失敗, 什麼都不留。
        """
        host = host or self.local_host
        if not cwd:
            raise ValueError("a project path is required")
        if kind == "note" and not ident:
            ident = store.next_note_ref(cwd, host)
        if kind == "journal" and not ident:
            raise ValueError("a journal entry needs a date")
        store.validate(kind, ident)
        # journal 的標題就是日期, 不吃傳進來的 title
        row = sync.save_entry(self.cloud, kind, cwd, host, ident, text or "",
                              title=None if kind == "journal" else title) or {}

        path, warn = None, None
        if kind == "note":
            path, warn = self.export(kind, host, cwd, ident, text or "",
                                     row.get("title") or "")
        return {"ref": ident, "path": path, "warn": warn,
                "title": row.get("title") or ""}

    def export(self, kind, host, cwd, ref, body, title=""):
        """把一則 note 寫成專案裡的 markdown。專案在哪台機器就寫哪台。"""
        if kind != "note":
            raise ValueError("only notes are written into the project folder")
        sub = entries.NOTE_DIR
        target = f"{cwd}/{sub}/{ref}.md"
        try:
            if host == self.local_host:
                if not Path(cwd).is_dir():
                    return None, f"Saved. {cwd} is gone, so no markdown was written."
                entries.export_entry(kind, cwd, ref, body, host, title)
            else:
                text = entries.note_text(cwd, ref, body, host, title)
                q = shell_quote
                p = subprocess.run(
                    ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
                     f"mkdir -p {q(f'{cwd}/{sub}')} && cat > {q(target)}"],
                    input=text.encode("utf-8"), capture_output=True, timeout=45)
                if p.returncode != 0:
                    err = (p.stderr or b"").decode("utf-8", "replace").strip()
                    return None, f"Saved, but could not write markdown on {host}: {err or 'ssh failed'}"
        except (OSError, subprocess.SubprocessError) as e:
            return None, f"Saved, but could not write the markdown copy: {e}"
        store.mark_exported(kind, cwd, host, ref, target)
        return target, None

    def delete(self, kind, host, cwd, ident):
        host = host or self.local_host
        if kind != "note":
            raise ValueError("only notes can be deleted")
        sync.remove_entry(self.cloud, kind, cwd, host, ident)
        warn = None
        try:
            if host == self.local_host:
                entries.note_path(cwd, ident).unlink(missing_ok=True)
            else:
                subprocess.run(
                    ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
                     f"rm -f {shell_quote(f'{cwd}/{entries.NOTE_DIR}/{ident}.md')}"],
                    capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as e:
            warn = f"Deleted, but the markdown file could not be removed: {e}"
        return warn
