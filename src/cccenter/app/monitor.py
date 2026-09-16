"""
The watcher: keeps this window of work in memory and tells the page when it moves.

One loop, two clocks. Local transcripts are cheap to check, so it looks at their
mtimes every few seconds and only rescans when something actually changed.
Remote machines cost an ssh round trip, so they are collected on a timer, faster
for the ones that were busy recently.

The interesting judgement is `alive`: whether a session that stopped is waiting
for you or simply over. `claude` does not hold its transcript open, so a process
can only be matched to a *folder*, never to a session --- which means "there is a
process in this cwd" is necessary but not sufficient. Two more rules make it
usable: a session cannot belong to a process that started after the session's own
last line, and a folder can only have as many live sessions as it has processes,
newest first. See `alive_index` and `alive_of`.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from .. import analysis, scanner, store, sync
from .. import cloud as cloudmod
from . import control, journal
from .attention import attention_of
from .bus import BUS
from .daemon import read_pidfile
from .desktop import notify
from .paths import SCANNER
from .settings import read_hosts, read_settings, tz_offset
from .usage import USAGE_FILE
from .util import now, parse_iso, shell_quote
from .writing import Writer


class Monitor:
    """目前這個時間視窗裡的所有 session, 以及誰還活著。

    整個 process 共用一顆 (下面的 MON)。所有會動到狀態的地方都在 self.lock 裡面,
    因為 scanner 的 TZ 是 module global, 兩條執行緒同時換時區會互相踩到。
    """

    def __init__(self):
        self.lock = threading.RLock()        # cc-center 的 TZ 是 module global
        self.by_host: dict[str, list] = {}
        self.procs: dict[str, list] = {}          # host -> 還活著的 claude
        self.host_state: dict[str, dict] = {}     # host -> {status, ms, error, at, n}
        self.orphans: dict[str, dict] = {}         # host -> {cwd: [對不到提問的 commit]}
        self.projects: dict[str, list] = {}        # host -> [所有專案, 不管有沒有活動]
        self.usage: dict[str, dict] = {}           # host -> statusline 留下的方案用量快照
        self.daily: dict[str, dict] = {}           # host -> {day: token / 花費總量}, 不看視窗
        self.daily_cache: dict = {}                # 本機 transcript 沒改就不重讀
        self.alive_idx: dict = {}                  # (host, cwd) -> 允許算活著的 session_id
        self.alive_idx_rev = -1
        self.rev = 0
        self.window = None
        self.local_host = socket.gethostname().split(".")[0]
        self.last_local_scan = 0.0
        self.last_remote_scan = 0.0
        self.host_last: dict[str, float] = {}
        self.local_fingerprint = None
        self.busy = set()
        self.log: list[dict] = []
        self.attention: dict[tuple, str] = {}     # (host, session_id) -> 已經講過的 kind
        self.seen_once = False                    # 開機第一輪只記錄, 不通知
        self.cloud = cloudmod.default()           # Supabase: 你寫的字的真相
        self.writer = Writer(self.local_host, self.say, self.cloud)   # journal / note 的存檔
        self.journal_runs: dict[str, dict] = {}   # day -> {at, n, done}; 早上自動產 journal 的進度
        self.journal_busy = False
        self.journal_nagged = False               # 「沒登入所以沒寫 journal」只講一次
        self.last_sync = 0.0                      # 上一次跟雲端對過 (成功或失敗)
        self.sync_busy = False
        self.sync_error = None                    # 上一次 sync 的錯, 給介面看

    # -- log

    def say(self, msg, level="info", host=None):
        entry = {"t": now(), "level": level, "msg": msg, "host": host}
        with self.lock:
            self.log.append(entry)
            del self.log[:-300]
        BUS.emit("log", entry)
        # 也印到 stdout: CLI 版進 app.log, 桌面 app 由殼收進同一個檔 —— 別人的機器
        # 出事時, 這是唯一看得到的東西
        try:
            print(f"[{level}]{f' [{host}]' if host else ''} {msg}", flush=True)
        except OSError:
            pass

    # -- 視窗

    def compute_window(self, st):
        scanner.TZ = scanner.parse_tz(st["tz"])
        lo, hi, wanted = scanner.window_from_args(int(st["days"] or 1), st["date"] or None, None, None)
        return lo, hi, wanted

    # -- 本機

    def local_changed(self) -> bool:
        """看一眼 transcript 的 mtime, 沒動就不用重掃。"""
        root = scanner.projects_dir()
        newest, count = 0.0, 0
        try:
            for p in root.glob("*/*.jsonl"):
                try:
                    m = p.stat().st_mtime
                except OSError:
                    continue
                count += 1
                if m > newest:
                    newest = m
        except OSError:
            return False
        # statusline 鉤子寫的用量快照也算「本機有動」, 額度變了畫面才會跟著動
        try:
            newest = max(newest, USAGE_FILE.stat().st_mtime)
        except OSError:
            pass
        fp = (round(newest, 3), count)
        if fp == self.local_fingerprint:
            return False
        self.local_fingerprint = fp
        return True

    def scan_local(self, st, reason=""):
        with self.lock:
            self.busy.add(self.local_host)
        BUS.emit("hosts", self.hosts_snapshot())
        t0 = now()
        adopted = 0
        try:
            with self.lock:
                lo, hi, wanted = self.compute_window(st)
                sessions = scanner.collect(self.local_host, lo, hi, wanted, st["sidechains"],
                                      set(st["entrypoints"]) if st["entrypoints"] else None,
                                      st.get("oneshot", False))
                self.by_host[self.local_host] = sessions
                self.orphans[self.local_host] = scanner.link_commits(sessions, lo, hi)
                projects = scanner.all_projects()
                self.projects[self.local_host] = projects
                self.usage[self.local_host] = scanner.read_usage() or {}
                self.daily[self.local_host] = scanner.daily_totals(self.daily_cache)
                adopted = self.writer.adopt([p["cwd"] for p in projects if p["exists"]],
                                            self.local_host)
                self.procs[self.local_host] = scanner.running_claude()
                self.window = {"since": lo.astimezone(timezone.utc).isoformat(),
                               "until": hi.astimezone(timezone.utc).isoformat(),
                               "tz": st["tz"],
                               "days": sorted(wanted) if wanted else None}
                self.host_state[self.local_host] = {
                    "status": "ok", "at": now(), "n": len(sessions),
                    "ms": int((now() - t0) * 1000), "local": True}
                self.last_local_scan = now()
                self.rev += 1
        except SystemExit as e:
            with self.lock:
                self.host_state[self.local_host] = {"status": "error", "at": now(),
                                                    "error": str(e), "local": True}
            self.say(f"Local scan failed: {e}", "error", self.local_host)
        except Exception as e:                      # noqa: BLE001
            with self.lock:
                self.host_state[self.local_host] = {"status": "error", "at": now(),
                                                    "error": str(e), "local": True}
            self.say(f"Local scan failed: {type(e).__name__}: {e}", "error", self.local_host)
        finally:
            with self.lock:
                self.busy.discard(self.local_host)
        self.push(reason or "local")
        if adopted and self.cloud.signed_in:
            # 資料夾裡收進來的舊檔別等五分鐘, 現在就推上去
            threading.Thread(target=self.sync_now, args=("adopted files",), daemon=True).start()

    # -- 遠端

    def fetch_remote(self, host, st, since, until):
        with self.lock:
            self.busy.add(host)
        BUS.emit("hosts", self.hosts_snapshot())
        t0 = now()
        extra = []
        if st["sidechains"]:
            extra.append("--sidechains")
        if st.get("oneshot"):
            extra.append("--oneshot")
        for e in st["entrypoints"]:
            extra += ["--entrypoint", e]
        cmd = ("$(command -v python3 || command -v python) - --json "
               f"--host {shell_quote(host)} --tz {shell_quote(tz_offset(st['tz']))} "
               f"--since {shell_quote(since)} --until {shell_quote(until)} "
               + " ".join(shell_quote(x) for x in extra))
        timeout = int(st["ssh_timeout"] or 8)
        state = {"status": "error", "at": now(), "ms": None, "error": None}
        try:
            p = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}", host, cmd],
                input=SCANNER.read_bytes(), capture_output=True,
                timeout=max(timeout, 20) + 180)
            state["ms"] = int((now() - t0) * 1000)
            if p.returncode != 0 or not p.stdout.strip():
                err = (p.stderr or b"").decode("utf-8", "replace").strip().splitlines()
                state["error"] = err[-1] if err else "no output"
                self.say(f"{host}: {state['error']}", "error", host)
            else:
                blob = json.loads(p.stdout.decode("utf-8", "replace"))
                sessions = [scanner.from_json(d) for d in blob.get("sessions", []) if d.get("days")]
                with self.lock:
                    self.by_host[host] = sessions
                    self.orphans[host] = blob.get("repo_commits") or {}
                    self.projects[host] = blob.get("projects") or []
                    self.procs[host] = blob.get("processes") or []
                    self.usage[host] = blob.get("usage") or {}
                    self.daily[host] = blob.get("daily") or {}
                    self.rev += 1
                state.update(status="ok", n=len(sessions), error=None)
            if state["status"] != "ok":
                self.procs.pop(host, None)      # 收不到就當「不知道」, 別用舊資料判死活
        except subprocess.TimeoutExpired:
            state["error"] = f"timed out (>{timeout}s)"
            self.say(f"{host}: timed out", "error", host)
        except json.JSONDecodeError as e:
            state["error"] = f"reply was not JSON: {e}"
            self.say(f"{host}: {state['error']}", "error", host)
        except OSError as e:
            state["error"] = str(e)
            self.say(f"{host}: {e}", "error", host)
        finally:
            with self.lock:
                self.busy.discard(host)
                old = self.host_state.get(host) or {}
                if state["status"] == "error" and old.get("status") == "ok":
                    state["n"] = old.get("n")       # 保留上一次的數字, 別讓畫面歸零
                self.host_state[host] = state
        return state

    def scan_remotes(self, st, hosts=None, reason="remote"):
        hosts = hosts if hosts is not None else self.active_hosts(st)
        if not hosts:
            self.last_remote_scan = now()
            return
        with self.lock:
            if not self.window:
                self.compute_window(st)
            lo, hi, wanted = self.compute_window(st)
            since = lo.astimezone(timezone.utc).isoformat()
            until = hi.astimezone(timezone.utc).isoformat()
        threads = [threading.Thread(target=self.fetch_remote, args=(h, st, since, until),
                                    daemon=True) for h in hosts]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        stamp = now()
        for h in hosts:
            self.host_last[h] = stamp
        self.last_remote_scan = stamp
        self.push(reason)

    def due_hosts(self, st):
        """該收了的遠端。最近還有人在動的那幾台用比較短的間隔。"""
        slow = float(st["remote_poll"] or 0)
        fast = float(st["remote_poll_hot"] or 0) or slow
        if not slow or not st.get("remote_enabled", True):
            return []
        warm = now() - float(st["live_window"] or 600) * 2
        hot = set()
        with self.lock:
            for host, sessions in self.by_host.items():
                for s in sessions:
                    at = parse_iso((s.get("tail") or {}).get("at"))
                    if at and at >= warm:
                        hot.add(host)
                        break
        ref = now()
        return [h for h in self.active_hosts(st)
                if ref - self.host_last.get(h, 0.0) >= (min(fast, slow) if h in hot else slow)]

    def active_hosts(self, st):
        off = set(st.get("disabled_hosts") or [])
        return [h for h in read_hosts() if h not in off and h != self.local_host]

    # -- 對外

    def forget(self, hosts):
        with self.lock:
            for h in hosts:
                self.by_host.pop(h, None)
                self.host_state.pop(h, None)
                self.usage.pop(h, None)
                self.daily.pop(h, None)
                self.procs.pop(h, None)
                self.orphans.pop(h, None)
                self.projects.pop(h, None)
            self.rev += 1

    def alive_index(self):
        """(host, cwd) -> 允許算活著的 session_id。None 表示那台不知道, 不設限。

        一個 cwd 有幾顆 process, 最多就只有幾個 session 真的在跑。同一顆 process
        的生命週期裡開過的那些 session 是**同一個 agent** —— VS Code 續開一次就
        多一個 session 檔, 但人只有一個, 不該一人發一張「在等你」。誰是那個, 用
        最後活動時間決定: 你最後碰過的那個才是你正在看的那個。
        """
        with self.lock:
            if self.alive_idx_rev == self.rev:
                return self.alive_idx
            groups: dict = {}
            for sess in self.sessions():
                cwd = (sess.get("cwd") or "").rstrip("/")
                if cwd:
                    groups.setdefault((sess.get("host"), cwd), []).append(sess)
            idx = {}
            for (host, cwd), lst in groups.items():
                procs = self.procs.get(host)
                if procs is None or any(p.get("cwd") is None for p in procs):
                    idx[(host, cwd)] = None          # 收不到 / 讀不到 cwd, 不敢說死
                    continue
                here = [p for p in procs if (p.get("cwd") or "").rstrip("/") == cwd]
                lst.sort(key=lambda x: parse_iso((x.get("tail") or {}).get("at")) or 0,
                         reverse=True)
                idx[(host, cwd)] = {x.get("session_id") for x in lst[:len(here)]}
            self.alive_idx, self.alive_idx_rev = idx, self.rev
            return idx

    def alive_of(self, sess):
        """這個 session 的 cwd 底下還有沒有 claude 活著。

        對應鍵只能用 cwd —— claude 不會一直開著 transcript 的 fd, 對不到 session id。
        所以精度是「這個專案還有沒有 agent 活著」, 同一個 cwd 有兩個 session 就分不開。
        """
        procs = self.procs.get(sess.get("host"))
        if procs is None:
            return None
        cwd = (sess.get("cwd") or "").rstrip("/")
        if not cwd:
            return None
        if any(p.get("cwd") is None for p in procs):
            return None                      # 有 process 但讀不到 cwd, 不敢說死
        here = [p for p in procs if (p.get("cwd") or "").rstrip("/") == cwd]
        if not here:
            return False
        allowed = self.alive_index().get((sess.get("host"), cwd))
        if allowed is not None and sess.get("session_id") not in allowed:
            return False                     # 這個 cwd 的名額已經被更近的 session 佔走
        # cwd 對得上還不夠: 一個資料夾跑過的每個 session 都會對到同一顆 process。
        # 真正在跑的那個, 它的最後一筆一定在 process 起來之後 —— 比 process 還舊的
        # session 是在它出生前就結束的, 不可能是它。讀不到 started 就退回只看 cwd。
        last = parse_iso((sess.get("tail") or {}).get("at"))
        if last is None or all(p.get("started") is None for p in here):
            return True
        return any(p.get("started") is None or p["started"] <= last for p in here)

    def hosts_snapshot(self):
        with self.lock:
            out = []
            for h, s in self.host_state.items():
                d = dict(s)
                d["host"] = h
                d["busy"] = h in self.busy
                procs = self.procs.get(h)
                d["procs"] = None if procs is None else len(procs)
                out.append(d)
            for h in self.busy:
                if h not in self.host_state:
                    out.append({"host": h, "status": "pending", "busy": True})
            return {"hosts": out, "rev": self.rev,
                    "last_local": self.last_local_scan, "last_remote": self.last_remote_scan}

    def push(self, reason=""):
        BUS.emit("update", {"rev": self.rev, "reason": reason,
                            "hosts": self.hosts_snapshot()["hosts"],
                            "last_local": self.last_local_scan,
                            "last_remote": self.last_remote_scan})

    def bump(self, reason=""):
        """有人寫了東西 (存了一則 journal/note): 換一個版本號並通知每個分頁。"""
        with self.lock:
            self.rev += 1
        self.push(reason)

    def sessions(self):
        with self.lock:
            return [s for host in sorted(self.by_host) for s in self.by_host[host]]

    # -- 雲端

    def auth_info(self):
        """登入了沒、是誰、上次同步。不打網路, 離線也答得出來。"""
        me = self.cloud.account() or {}
        st = store.stats()
        return {"configured": self.cloud.configured,
                "project": self.cloud.project,
                "signed_in": self.cloud.signed_in,
                "email": me.get("email"),
                "synced_at": st.get("synced_at"),
                "pending": st.get("pending") or 0,
                "error": self.sync_error}

    def sync_now(self, reason=""):
        """跟雲端對一次: 收下來、把本機獨有的推上去。一次一條執行緒。"""
        if self.sync_busy:
            return None
        self.sync_busy = True
        try:
            if not self.cloud.signed_in:
                return None
            try:
                out = sync.pull(self.cloud, now())
            except cloudmod.Unreachable as e:
                self.sync_error = str(e)
                self.say(f"sync: {e} — showing the local copy", "warn")
                return None
            except cloudmod.NotSignedIn as e:
                self.sync_error = str(e)
                self.say(f"sync: {e}", "warn")
                self.bump("signed out")
                return None
            except cloudmod.CloudError as e:
                self.sync_error = str(e)
                self.say(f"sync: {e}", "error")
                return None
            self.sync_error = None
            if out["owner_changed"]:
                self.writer.adopted.clear()         # 新帳號: 資料夾裡的檔要再收一次
                self.say("sync: a different account signed in, the local copy was replaced", "warn")
            moved = out["pushed"] or out["removed"] or out["owner_changed"]
            self.say(f"sync ({reason}): {out['pulled']} entries from the cloud"
                     + (f", {out['pushed']} sent up" if out["pushed"] else "")
                     + (f", {out['removed']} removed" if out["removed"] else ""),
                     "info")
            self.bump("sync") if moved else self.push("sync")
            return out
        finally:
            self.last_sync = now()
            self.sync_busy = False

    # -- 早上自動寫昨天的 journal

    def journal_tick(self, st):
        """時間過了、還沒產齊就開一條去寫。一次只跑一天、一條執行緒。"""
        if self.journal_busy:
            return
        now_local = datetime.now(scanner.parse_tz(st["tz"]))
        due = journal.due_days(now_local, st, self.journal_runs)
        if not due:
            return
        if not self.cloud.signed_in:
            # 寫出來也存不進去 (雲端才是真相), 別白花 claude 的錢; 登入後下一輪就會補
            if not self.journal_nagged:
                self.journal_nagged = True
                self.say(f"journal {due[0]}: not written, sign in first", "warn")
            return
        self.journal_nagged = False
        self.journal_busy = True
        threading.Thread(target=self._journal_run, args=(due[0], st), daemon=True).start()

    def _journal_run(self, day, st):
        try:
            # 遠端總開關關著 (下班連不到) 就只寫本機的, 別去撞 ssh 逾時
            hosts = self.active_hosts(st) if st.get("remote_enabled", True) else []
            results = journal.write_day(day, st, self.writer, hosts, self.local_host,
                                        log=self.say, lock=self.lock)
            run = journal.record_run(self.journal_runs, day, results)
            n = sum(1 for r in results if r["status"] == "written")
            if n:
                self.bump("journal")
            if not run["done"]:
                self.say(f"journal {day}: not finished (attempt {run['n']}), "
                         f"will retry in {journal.RETRY_AFTER // 60} min", "warn")
        except Exception as e:                    # noqa: BLE001 - 排程不能把迴圈弄死
            journal.record_run(self.journal_runs, day, [{"status": "error"}])
            self.say(f"journal {day}: {type(e).__name__}: {e}", "error")
        finally:
            self.journal_busy = False

    def check_attention(self, st):
        """每個 tick 都算一次 (不用重掃, tail 不會自己變), 狀態有變才通知。"""
        scope = st.get("notify_scope") or "remote"
        ref = now()
        current = {}
        fresh = []
        for s in self.sessions():
            a = attention_of(s, st, ref, self.alive_of(s))
            if not a:
                continue
            key = (s.get("host"), s.get("session_id"))
            current[key] = a["kind"]
            if self.attention.get(key) != a["kind"]:
                fresh.append((s, a))

        gone = [k for k in self.attention if k not in current]
        self.attention = current
        if fresh or gone:
            BUS.emit("attention", {"count": len(current)})
        if not self.seen_once:
            self.seen_once = True         # 開機那一輪不要一次噴一堆通知
            return
        if scope == "off":
            return
        url = None
        info = read_pidfile()
        if info:
            url = info.get("url")
        for s, a in fresh:
            host = s.get("host")
            if scope == "remote" and host == self.local_host:
                continue
            title = scanner.title_of(s)
            self.say(f"{a['label']}: {title}", "warn", host)
            head = (f"{a['label']}"
                    + (f" ({a['tool']})" if a["kind"] != "waiting" and a["tool"] else ""))
            sub = f"{host} · {Path(s.get('cwd') or '').name}"
            if os.environ.get("CC_CENTER_APP"):
                # 桌面 app: 殼用自己的身分發通知 (點了會回到視窗), 這裡只把內容推過去
                BUS.emit("notify", {"title": head, "subtitle": sub, "body": title,
                                    "host": host, "session_id": s.get("session_id"),
                                    "sound": bool(st.get("notify_sound"))})
            else:
                notify(head, sub, title, bool(st.get("notify_sound")), url)

    # -- 對 session 動手

    def find_session(self, host, session_id):
        with self.lock:
            for s in self.by_host.get(host) or []:
                if s.get("session_id") == session_id:
                    return s
        return None

    def signal_session(self, host, session_id, sig, st):
        """Interrupt (INT) 或 End (TERM) 一個 session 的 claude。成功後很快重掃那台。"""
        sess = self.find_session(host, session_id)
        if not sess:
            raise ValueError("That session is not in the current window any more.")
        out = control.signal_session(sess, self.procs.get(host), sig, self.local_host,
                                     timeout=int(st.get("ssh_timeout") or 8))
        self.say(f"sent SIG{sig} to claude (pid {out['pid']}): {scanner.title_of(sess)}",
                 "info", host)

        def soon():
            threading.Event().wait(2.0)
            if host == self.local_host:
                self.local_fingerprint = None
                self.scan_local(st, f"after SIG{sig}")
            else:
                self.scan_remotes(st, [host], f"after SIG{sig}")
        threading.Thread(target=soon, daemon=True).start()
        return out

    def live(self, st):
        """狀態列選單用的短清單 (在等你的 / 在跑的)。"""
        with self.lock:
            return control.live(self.sessions(), st, self.alive_of)

    def plan_usage(self):
        """額度是帳號的, 不分機器 —— 哪台的快照最新就用哪台的。"""
        best, best_host = None, None
        for host, snap in self.usage.items():
            if snap and snap.get("at") and (best is None or snap["at"] > best["at"]):
                best, best_host = snap, host
        if not best:
            return None
        return {"at": best["at"], "host": best_host, "model": best.get("model"),
                "rate_limits": best.get("rate_limits") or {}}

    def daily_merged(self):
        """每台機器的每日總量加起來; 每天另外記各台各花了多少, 滑過去看得到。"""
        out: dict[str, dict] = {}
        for host, days in self.daily.items():
            for day, d in days.items():
                m = out.get(day)
                if m is None:
                    m = out[day] = {"calls": 0, "in": 0, "out": 0, "cache_read": 0,
                                    "cache_write": 0, "cost": 0.0, "models": {}, "hosts": {}}
                for k in ("calls", "in", "out", "cache_read", "cache_write", "cost"):
                    m[k] += d.get(k) or 0
                for model, c in (d.get("models") or {}).items():
                    m["models"][model] = round(m["models"].get(model, 0.0) + c, 4)
                m["hosts"][host] = round(d.get("cost") or 0.0, 4)
        for m in out.values():
            m["cost"] = round(m["cost"], 4)
        return out

    def known_ctx(self):
        """statusline 報過的每個 session 的 context, 它知道的視窗大小比用猜的準。"""
        out = {}
        for snap in self.usage.values():
            for sid, c in (snap.get("sessions") or {}).items():
                if c.get("size") and (sid not in out or c.get("at", 0) > out[sid].get("at", 0)):
                    out[sid] = c
        return out

    def payload(self, st):
        with self.lock:
            scanner.TZ = scanner.parse_tz(st["tz"])
            sessions, ref = [], now()
            known = self.known_ctx()
            for s in self.sessions():
                d = scanner.to_json(s)
                for key, day in d.get("days", {}).items():
                    day["cost"] = round(analysis.day_cost(s, key), 4)
                d["title"] = scanner.title_of(s)
                alive = self.alive_of(s)
                d["alive"] = alive
                d["attention"] = attention_of(s, st, ref, alive)
                seen = known.get(s["session_id"])
                if seen and d.get("ctx"):
                    d["ctx"]["size"] = seen["size"]
                sessions.append(d)
            return {
                "rev": self.rev,
                "window": self.window,
                "usage": self.plan_usage(),
                "daily": self.daily_merged(),
                "host_list": read_hosts(),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "server_now": now(),
                "local_host": self.local_host,
                "hosts": self.hosts_snapshot()["hosts"],
                "last_local": self.last_local_scan,
                "last_remote": self.last_remote_scan,
                "repo_commits": self.repo_commits(),
                "projects": self.project_list(),
                "entries": store.by_project(),
                "store": store.stats(),
                "auth": self.auth_info(),
                "changes": self.changes(),
                "sessions": sessions,
            }

    def repo_commits(self):
        """各機器對不到任何提問的 commit, 攤成 {host: {cwd: [...]}}。"""
        return {h: v for h, v in self.orphans.items() if v}

    def project_list(self):
        """每台機器上 ~/.claude/projects 底下的所有專案, 攤平成一份清單。"""
        out = []
        for host, lst in (self.projects or {}).items():
            for p in lst or []:
                out.append({**p, "host": host})
        out.sort(key=lambda p: -(p.get("last_active") or 0))
        return out

    def changes(self):
        """每個專案每一天「到底改了什麼」—— 算出來的, 介面跟 journal 用同一份。"""
        out = {}
        seen = set()
        for s in self.sessions():
            cwd = s.get("cwd")
            if not cwd:
                continue
            for day in s["days"]:
                if (cwd, day) in seen:
                    continue
                seen.add((cwd, day))
                orph = (self.repo_commits().get(s["host"]) or {}).get(cwd)
                ch = analysis.day_changes(self.sessions(), cwd, day, orph)
                ch.pop("cwd", None)
                out.setdefault(cwd, {})[day] = ch
        return out


MON = Monitor()


CLOUD_POLL = 300      # 秒; 另一台機器寫的字最多這麼久後出現在這裡


def monitor_loop(stop_event: threading.Event):
    """常駐: 本機看 mtime, 遠端看時間到了沒, 雲端每幾分鐘對一次。"""
    first = True
    while not stop_event.is_set():
        st = read_settings()
        try:
            lp = int(st["local_poll"] or 0)
            rp = int(st["remote_poll"] or 0)
            if not st["include_local"]:
                if MON.local_host in MON.by_host:
                    MON.forget([MON.local_host])
                    MON.push("stopped watching this machine")
            elif first or (lp and now() - MON.last_local_scan >= lp and MON.local_changed()):
                MON.scan_local(st, "first scan" if first else "new local activity")
            due = MON.due_hosts(st) if rp else []
            if due and not first:
                MON.scan_remotes(st, due, "scheduled remote collection")
            elif first and st.get("remote_enabled", True):
                MON.last_remote_scan = now()      # 開機先不擋畫面, 遠端另開一條去收
                threading.Thread(target=MON.scan_remotes, args=(st,),
                                 kwargs={"reason": "first remote collection"}, daemon=True).start()
            MON.check_attention(st)
            if first or (MON.cloud.signed_in and now() - MON.last_sync >= CLOUD_POLL):
                MON.last_sync = now()
                threading.Thread(target=MON.sync_now,
                                 args=("startup" if first else "scheduled",),
                                 daemon=True).start()
            if not first:
                MON.journal_tick(st)
        except Exception as e:                    # noqa: BLE001 - 迴圈不能死
            MON.say(f"Watch loop: {type(e).__name__}: {e}", "error")
        first = False
        stop_event.wait(3)


# ---------------------------------------------------------------- 一次性工作 (AI 日誌)


def ssh_probe(host, timeout):
    t0 = now()
    try:
        p = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}", host,
             "command -v python3 || command -v python"],
            capture_output=True, text=True, timeout=timeout + 6)
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"host": host, "status": "error", "error": f"{type(e).__name__}"}
    ms = int((now() - t0) * 1000)
    if p.returncode != 0:
        err = (p.stderr or "").strip().splitlines()
        return {"host": host, "status": "error", "ms": ms,
                "error": err[-1] if err else "ssh could not connect"}
    if not (p.stdout or "").strip():
        return {"host": host, "status": "error", "ms": ms, "error": "no python3 on that machine"}
    return {"host": host, "status": "ok", "ms": ms, "python": p.stdout.strip().splitlines()[0]}
