"""
The local copy of what you wrote.

Everything else in this package can be thrown away and recomputed by reading the
transcripts again. What you wrote cannot, so it lives in Supabase (`cloud`), and
this SQLite file is the cache of it on this machine:

    ~/.cc-center/cc-center.db

Reads come from here, so the page is fast and still works offline. Writes go to
Supabase first and land here only once the cloud has them (`sync.save_entry`);
`sync.pull` brings the cloud's rows down and pushes up the few that were never
there --- files adopted from a project folder, or rows from before the cloud.
`synced_at` on a row is the difference: NULL means the cloud has not seen it.

The `note/*.md` files in a project are a projection written on save (see
`cccenter.entries`) so a note travels with the repo. The Markdown is never
parsed back, except once per project on first sight, to adopt files that were
there before this tool was. This module is the only thing that touches SQLite.

Standard library only.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .entries import JOURNAL_DIR, NOTE_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id           INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL,             -- 'journal' | 'note'
    cwd          TEXT NOT NULL,             -- 專案路徑
    host         TEXT NOT NULL,             -- 專案在哪台機器
    ref          TEXT NOT NULL,             -- journal: YYYY-MM-DD; note: YYYY-MM-DD-HHMM[-n]
    day          TEXT NOT NULL,             -- 歸到哪一天 (note 也有, 好跟當天的紀錄擺一起)
    title        TEXT NOT NULL DEFAULT '',  -- note 專用; journal 的標題就是日期
    body         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    exported_at  TEXT,
    exported_path TEXT,
    cloud_id     TEXT,                     -- Supabase 那一列的 id
    synced_at    TEXT,                     -- NULL = 雲端還沒有這一列
    UNIQUE(kind, cwd, host, ref)
);
CREATE INDEX IF NOT EXISTS ix_entries_cwd_day ON entries(cwd, day);
CREATE INDEX IF NOT EXISTS ix_entries_kind_day ON entries(kind, day);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

_LOCK = threading.RLock()
_CONN = None
_PATH = None

NOTE_REF = re.compile(r"\A\d{4}-\d{2}-\d{2}-\d{4}(-\d+)?\Z")
DAY_REF = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")


def db_path() -> Path:
    return Path(os.environ.get("CC_CENTER_STATE",
                               Path.home() / ".cc-center")) / "cc-center.db"


def connect(path=None):
    """整個 process 共用一條連線 (check_same_thread=False + 自己的鎖)。"""
    global _CONN, _PATH
    with _LOCK:
        want = Path(path or db_path())
        if _CONN is not None and _PATH == want:
            return _CONN
        if _CONN is not None:                 # 換了路徑 (只有測試會): 舊的關掉
            _CONN.close()
        want.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(want), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(entries)")}
        # 舊的 DB, 補上後來才有的欄
        if "title" not in cols:
            conn.execute("ALTER TABLE entries ADD COLUMN title TEXT NOT NULL DEFAULT ''")
        if "cloud_id" not in cols:
            conn.execute("ALTER TABLE entries ADD COLUMN cloud_id TEXT")
        if "synced_at" not in cols:
            conn.execute("ALTER TABLE entries ADD COLUMN synced_at TEXT")
        conn.commit()
        _CONN, _PATH = conn, want
        return conn


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def validate(kind, ref):
    if kind == "journal" and not DAY_REF.match(ref or ""):
        raise ValueError(f"a journal ref must look like YYYY-MM-DD, got {ref!r}")
    if kind == "note" and not NOTE_REF.match(ref or ""):
        raise ValueError(f"a note ref must look like YYYY-MM-DD-HHMM, got {ref!r}")
    if kind not in ("journal", "note"):
        raise ValueError(f"kind must be journal or note, got {kind!r}")


def put(kind, cwd, host, ref, body, day=None, title=None, cloud=None):
    """新增或覆寫一則快取。回傳整列。

    title=None 表示「這次不動標題」, 傳空字串才是真的清掉。

    cloud 是 Supabase 回來的那一列: 給了就照它的 id 跟時間記, 並標成已同步;
    沒給就是只有本機知道的字 (收進來的舊 markdown), 等下一次 sync 推上去。
    """
    validate(kind, ref)
    day = day or ref[:10]
    now = _now()
    cloud = cloud or {}
    with _LOCK:
        c = connect()
        c.execute("""
            INSERT INTO entries (kind, cwd, host, ref, day, title, body,
                                 created_at, updated_at, cloud_id, synced_at)
                 VALUES (:kind,:cwd,:host,:ref,:day,:title,:body,
                         :created,:updated,:cloud_id,:synced)
            ON CONFLICT(kind, cwd, host, ref)
              DO UPDATE SET body=excluded.body,
                            title=CASE WHEN :keep_title THEN entries.title
                                       ELSE excluded.title END,
                            day=excluded.day,
                            created_at=CASE WHEN :from_cloud THEN excluded.created_at
                                            ELSE entries.created_at END,
                            updated_at=excluded.updated_at,
                            cloud_id=COALESCE(excluded.cloud_id, entries.cloud_id),
                            synced_at=excluded.synced_at
        """, {"kind": kind, "cwd": cwd, "host": host, "ref": ref, "day": day,
              "title": title or "", "body": body,
              "created": cloud.get("created_at") or now,
              "updated": cloud.get("updated_at") or now,
              "cloud_id": cloud.get("id"),
              "synced": now if cloud else None,
              "from_cloud": 1 if cloud else 0,
              "keep_title": 1 if title is None else 0})
        c.commit()
    return get(kind, cwd, host, ref)


def absorb(row):
    """把雲端的一列原封不動收進快取 (id、標題、時間都照它的)。"""
    return put(row["kind"], row["cwd"], row["host"], row["ref"], row.get("body") or "",
               day=row.get("day") or row["ref"][:10], title=row.get("title") or "",
               cloud=row)


def mark_synced(kind, cwd, host, ref, cloud_id=None):
    with _LOCK:
        c = connect()
        c.execute("""UPDATE entries SET synced_at=?, cloud_id=COALESCE(?, cloud_id)
                      WHERE kind=? AND cwd=? AND host=? AND ref=?""",
                  (_now(), cloud_id, kind, cwd, host, ref))
        c.commit()


def pending():
    """雲端還沒有的那些: 收進來的舊 markdown, 或接上雲端以前寫的。"""
    with _LOCK:
        return [dict(r) for r in connect().execute(
            "SELECT * FROM entries WHERE synced_at IS NULL ORDER BY ref")]


def synced_keys():
    """雲端見過的每一列的 (kind, cwd, host, ref)。"""
    with _LOCK:
        return {(r["kind"], r["cwd"], r["host"], r["ref"]) for r in connect().execute(
            "SELECT kind, cwd, host, ref FROM entries WHERE synced_at IS NOT NULL")}


def wipe():
    """整份快取清掉 —— 換了帳號, 上一個人的字不能留給下一個人。"""
    with _LOCK:
        c = connect()
        c.execute("DELETE FROM entries")
        c.execute("DELETE FROM meta")
        c.commit()


def meta_get(key, default=None):
    with _LOCK:
        r = connect().execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return r["v"] if r else default


def meta_set(key, value):
    with _LOCK:
        c = connect()
        if value is None:
            c.execute("DELETE FROM meta WHERE k=?", (key,))
        else:
            c.execute("INSERT INTO meta (k, v) VALUES (?, ?) "
                      "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, str(value)))
        c.commit()


def mark_exported(kind, cwd, host, ref, path):
    with _LOCK:
        c = connect()
        c.execute("""UPDATE entries SET exported_at=?, exported_path=?
                      WHERE kind=? AND cwd=? AND host=? AND ref=?""",
                  (_now(), str(path), kind, cwd, host, ref))
        c.commit()


def get(kind, cwd, host, ref):
    with _LOCK:
        r = connect().execute("""SELECT * FROM entries
                                  WHERE kind=? AND cwd=? AND host=? AND ref=?""",
                              (kind, cwd, host, ref)).fetchone()
    return dict(r) if r else None


def delete(kind, cwd, host, ref):
    with _LOCK:
        c = connect()
        n = c.execute("""DELETE FROM entries
                          WHERE kind=? AND cwd=? AND host=? AND ref=?""",
                      (kind, cwd, host, ref)).rowcount
        c.commit()
    return n


def next_note_ref(cwd, host, when=None):
    """一則一個檔, ref 就是寫下的時間。同一分鐘內再寫就往後加號碼。"""
    stamp = (when or datetime.now().astimezone()).strftime("%Y-%m-%d-%H%M")
    with _LOCK:
        rows = connect().execute(
            """SELECT ref FROM entries
                WHERE kind='note' AND cwd=? AND host=? AND ref LIKE ?""",
            (cwd, host, stamp + "%")).fetchall()
    taken = {r["ref"] for r in rows}
    if stamp not in taken:
        return stamp
    for i in range(2, 1000):
        if f"{stamp}-{i}" not in taken:
            return f"{stamp}-{i}"
    raise RuntimeError("too many notes in the same minute")


def by_project(kind=None):
    """{cwd: [entry…]}, 新的在前。"""
    sql = "SELECT * FROM entries"
    args = ()
    if kind:
        sql += " WHERE kind=?"
        args = (kind,)
    sql += " ORDER BY ref DESC"
    out = {}
    with _LOCK:
        for r in connect().execute(sql, args):
            out.setdefault(r["cwd"], []).append(dict(r))
    return out


def for_project(cwd, kind=None):
    sql = "SELECT * FROM entries WHERE cwd=?"
    args = [cwd]
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    sql += " ORDER BY ref DESC"
    with _LOCK:
        return [dict(r) for r in connect().execute(sql, args)]


def search(q, limit=100):
    """內容搜尋。這個量級 LIKE 就夠快, 不用上 FTS。"""
    like = f"%{q}%"
    with _LOCK:
        return [dict(r) for r in connect().execute(
            """SELECT * FROM entries WHERE body LIKE ? OR title LIKE ?
                ORDER BY ref DESC LIMIT ?""", (like, like, limit))]


def previous_journal(cwd, host, before_ref):
    """上一則 journal。這次要整理的範圍就是從它之後到現在。"""
    with _LOCK:
        r = connect().execute(
            """SELECT * FROM entries
                WHERE kind='journal' AND cwd=? AND host=? AND ref<?
                ORDER BY ref DESC LIMIT 1""", (cwd, host, before_ref)).fetchone()
    return dict(r) if r else None


def days_with_journal(cwd=None):
    sql = "SELECT cwd, day FROM entries WHERE kind='journal'"
    args = ()
    if cwd:
        sql += " AND cwd=?"
        args = (cwd,)
    with _LOCK:
        return {(r["cwd"], r["day"]) for r in connect().execute(sql, args)}


def stats():
    with _LOCK:
        c = connect()
        row = c.execute("""SELECT
              (SELECT COUNT(*) FROM entries WHERE kind='journal')       AS journals,
              (SELECT COUNT(*) FROM entries WHERE kind='note')          AS notes,
              (SELECT COUNT(DISTINCT cwd) FROM entries)                 AS projects,
              (SELECT COUNT(*) FROM entries WHERE synced_at IS NULL)    AS pending""").fetchone()
        synced = meta_get("synced_at")
    out = dict(row)
    out.update({"path": str(_PATH or db_path()),
                "synced_at": float(synced) if synced else None})
    return out


# ---------------------------------------------------------------- 匯入舊的 markdown

_FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_NOTE_BLOCK = re.compile(r"^##\s*(?:In your words|我的說明)\s*$(.*?)(?=^##\s|\Z)",
                         re.M | re.S)


def _front_of(raw):
    m = _FRONT.match(raw)
    if not m:
        return {}
    out = {}
    for ln in m.group(1).splitlines():
        k, _, v = ln.partition(":")
        if v:
            out[k.strip()] = v.strip()
    return out


def _body_after_front(raw):
    m = _FRONT.match(raw)
    body = (raw[m.end():] if m else raw).lstrip("\n")
    return re.sub(r"\A#[^\n]*\n+", "", body).strip()


def import_markdown(cwd, host, journal_dir=JOURNAL_DIR, note_dir=NOTE_DIR):
    """把專案裡既有的 markdown 收進 DB。已經在 DB 裡的不動, 所以重跑安全。"""
    added = 0
    for sub, kind in ((journal_dir, "journal"), (note_dir, "note")):
        d = Path(cwd) / sub
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            ref = f.stem
            try:
                validate(kind, ref)
            except ValueError:
                continue                      # 不是這個工具寫的, 放著別動
            if get(kind, cwd, host, ref):
                continue
            try:
                raw = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if kind == "journal":
                # 舊的 journal 檔把你寫的字放在「## In your words」底下, 新的整份
                # 都是你寫的字, 所以找不到那個段落就整份收進來。
                m = _NOTE_BLOCK.search(raw)
                body = m.group(1).strip() if m else _body_after_front(raw)
                if body in ("_(not written yet)_", "_(還沒寫)_"):
                    body = ""
            else:
                body = _body_after_front(raw)
            front = _front_of(raw)
            # 標題優先讀 frontmatter, 舊檔沒有就退回 H1 (時間開頭的那種不算標題)
            title = front.get("title") or ""
            if not title and kind == "note":
                h1 = re.search(r"^#\s+(.+)$", raw, re.M)
                if h1 and not re.match(r"\A\d{4}-\d{2}-\d{2}\s", h1.group(1).strip()):
                    title = h1.group(1).strip()
            put(kind, cwd, front.get("host") or host, ref, body,
                day=ref[:10], title=title)
            mark_exported(kind, cwd, front.get("host") or host, ref, f)
            added += 1
    return added
