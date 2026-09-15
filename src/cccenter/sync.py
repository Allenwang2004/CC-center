"""
Keeping the local cache and Supabase telling the same story.

Two directions, one rule: the cloud is the truth.

    save_entry / remove_entry   one write, cloud first --- the cache only changes
                                once Supabase has accepted it, so a failed save
                                never leaves the page showing words the cloud
                                does not have
    pull                        the cloud's rows come down and replace what is
                                cached; rows the cloud lost are dropped here too;
                                rows only this machine knows (adopted Markdown,
                                writing from before the cloud) go up once,
                                without overwriting anything already there

`pull` is a full read every time. The table is a few hundred rows of your own
text, and one plain read is a lot easier to trust than incremental deltas plus a
tombstone table for deletes.

No I/O of its own beyond `store` and `cloud`; hand it a fake cloud and it runs
in a test.
"""

from __future__ import annotations

from . import store
from .cloud import CloudError, NotSignedIn

OWNER_KEY = "owner"           # meta: 快取現在是哪個帳號的
SYNCED_KEY = "synced_at"      # meta: 上一次 pull 成功的時間 (epoch)


def _key(r):
    return (r["kind"], r["cwd"], r["host"], r["ref"])


def _payload(kind, cwd, host, ref, body, title=None, created_at=None):
    row = {"kind": kind, "cwd": cwd, "host": host, "ref": ref, "day": ref[:10],
           "body": body or ""}
    if title is not None:
        row["title"] = title
    if created_at:
        row["created_at"] = created_at
    return row


def save_entry(cloud, kind, cwd, host, ref, body, title=None):
    """雲端先, 快取後。回傳快取那一列。

    title=None 表示「不動標題」: 雲端的 upsert 只更新有給的欄, 所以不放進去就好。
    """
    store.validate(kind, ref)
    row = cloud.upsert_entry(_payload(kind, cwd, host, ref, body, title))
    if not row:
        raise CloudError("Supabase accepted the row but did not return it")
    return store.absorb(row)                 # 雲端回的那一列才是真相, 標題也照它的


def remove_entry(cloud, kind, cwd, host, ref):
    """雲端刪掉才刪快取。雲端本來就沒有也算刪成功。"""
    cloud.delete_entry(kind, cwd, host, ref)
    return store.delete(kind, cwd, host, ref)


def pull(cloud, now):
    """把雲端整份收下來, 再把本機獨有的推上去。

    回傳 {"pulled", "pushed", "removed", "owner_changed"}。owner_changed=True 表示
    換了帳號, 快取整份清掉重來 —— 呼叫端要把「已經收過 markdown 的專案」那份記憶
    也清掉, 好讓資料夾裡的檔再收一次、推給新帳號。
    """
    me = cloud.account()
    if not me or not me.get("id"):
        raise NotSignedIn()
    owner_changed = False
    previous = store.meta_get(OWNER_KEY)
    if previous and previous != me["id"]:
        store.wipe()
        owner_changed = True

    rows = cloud.list_entries()                     # 連不上就在這裡停, 快取一個字都不動
    had = store.synced_keys()
    seen = set()
    for r in rows:
        try:
            store.absorb(r)
        except (KeyError, ValueError):
            continue                                # 不是這個工具寫的列, 放著
        seen.add(_key(r))
    removed = 0
    for key in had - seen:
        removed += store.delete(*key)

    pushed = 0
    for r in store.pending():
        payload = _payload(r["kind"], r["cwd"], r["host"], r["ref"], r["body"],
                           r.get("title") or "", r.get("created_at"))
        got = cloud.upsert_entry(payload, keep_existing=True)
        if got:
            store.absorb(got)
        else:
            # 雲端在 list 跟 push 之間長出了同一列: 它贏, 下一次 pull 會把它拿下來
            store.mark_synced(r["kind"], r["cwd"], r["host"], r["ref"])
        pushed += 1

    store.meta_set(OWNER_KEY, me["id"])
    store.meta_set(SYNCED_KEY, now)
    return {"pulled": len(seen), "pushed": pushed, "removed": removed,
            "owner_changed": owner_changed}
