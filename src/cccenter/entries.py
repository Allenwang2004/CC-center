"""
The Markdown projection of what you wrote.

Supabase is the truth for journal entries and notes (`cloud`, cached in
`store`); this module is the copy that lands in the project folder, so a note
travels with the repo and stays readable without this tool:

    <project>/note/2026-09-10-0340.md    one per thought

Journal entries used to land here too (`<project>/journal/2026-09-10.md`). They
no longer do: a journal is written by a machine every morning and belongs to the
account, not to the repo, so it would only clutter a project folder. The journal
functions below stay so that files written back then can still be read and
adopted once (`store.import_markdown`).

Nothing computed goes in these files. A note holds what you typed and nothing
else --- the day's diffs and commits are recomputed from the transcripts
whenever they are needed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .scanner import local, oneline

JOURNAL_DIR = "journal"       # 以前一天一份寫在這; 現在只讀不寫 (收進快取用)
NOTE_DIR = "note"             # 隨手: 想到什麼就記一則, 一則一個檔


def journal_path(cwd, day) -> Path:
    return Path(cwd) / JOURNAL_DIR / f"{day}.md"


def note_path(cwd, ref) -> Path:
    return Path(cwd) / NOTE_DIR / f"{ref}.md"


def _front(cwd, host, extra):
    lines = list(extra) + [f"project: {Path(cwd).name or cwd}"]
    if host:
        lines.append(f"host: {host}")
    lines.append(f"saved_at: {local(datetime.now(timezone.utc)).isoformat(timespec='seconds')}")
    lines.append("tool: cc-center")
    return "---\n" + "\n".join(lines) + "\n---\n\n"


def journal_text(cwd, day, note, host=None):
    """匯出到專案裡的那份 markdown。DB 才是真相, 這是給 git 跟人看的。

    只有你寫的字。這天改了什麼是掃 transcript 就重建得回來的東西, 寫進檔案只會
    讓每次存檔都長出一大塊你沒打過的內容 —— 那些留在介面的 Sessions 就好。
    """
    return (_front(cwd, host, [f"date: {day}"])
            + f"# {day} · {Path(cwd).name or cwd}\n\n"
            + (note or "").strip() + "\n")


def note_text(cwd, ref, text, host=None, title=""):
    """一則 note 的 markdown。有標題就用標題當 H1, 沒有就退回時間。"""
    when = f"{ref[:10]} {ref[11:13]}:{ref[13:15]}"
    title = oneline(title or "")
    front = [f"at: {ref}"] + ([f"title: {title}"] if title else [])
    head = title or f"{when} · {Path(cwd).name or cwd}"
    return (_front(cwd, host, front)
            + f"# {head}\n\n" + (text or "").strip() + "\n")


def export_entry(kind, cwd, ref, body, host=None, title=""):
    """把一則寫成專案裡的 markdown, 回傳路徑。"""
    path = journal_path(cwd, ref) if kind == "journal" else note_path(cwd, ref)
    text = (journal_text(cwd, ref, body, host) if kind == "journal"
            else note_text(cwd, ref, body, host, title))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)
