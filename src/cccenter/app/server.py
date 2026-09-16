"""
The HTTP surface: one page, a handful of JSON endpoints, and a live stream.

Bound to 127.0.0.1 only. Every `/api/*` call must carry the token that was
injected into the page it was served from, so another site open in the same
browser cannot read your work --- `EventSource` cannot set a header, so the
stream accepts the same token in the query string instead.

Behind the token there is a second gate: an account. What you wrote lives in
Supabase, so until this machine is signed in (`/api/auth/*`) the page is a
sign-in form and every other route answers 401. The browser never talks to
Supabase itself; `cccenter.cloud` does, from here.

Nothing is computed here. The handler parses a request, calls the watcher or the
writer, and turns the answer into JSON; if a route starts growing logic, the
logic belongs in `monitor` or `writing`.
"""

from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import os
import queue
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..cloud import CloudError
from . import claudemd
from .bus import BUS
from .daemon import clear_pidfile, write_pidfile
from .desktop import open_url
from .images import IMAGE_DIR, ImageError, Images
from .monitor import MON, monitor_loop, ssh_probe
from .paths import ROOT, STATE_DIR, WEB
from .settings import (
    migrate_old_names,
    read_hosts,
    read_settings,
    write_hosts,
    write_settings,
)
from .util import now

# 每次啟動換一把。頁面拿得到, 別的分頁拿不到。桌面 app 是它把 server 當 sidecar
# 帶起來的, 那把 token 就由它產生、用環境變數交進來, 它自己才打得進 /api/*。
TOKEN = os.environ.get("CC_CENTER_TOKEN") or secrets.token_urlsafe(24)
SERVER_STARTED = now()
MAX_BODY = 16 * 1024 * 1024          # 一張 10 MB 的圖 base64 之後再加 JSON, 差不多這麼大

_IMAGES = None


def images() -> Images:
    """journal 圖片的上傳與取回; 跟 MON 共用同一份登入態。"""
    global _IMAGES
    if _IMAGES is None:
        _IMAGES = Images(STATE_DIR / IMAGE_DIR, MON.cloud)
    return _IMAGES



# 前端是直接從磁碟端出去的, 改了檔案就該看到新的。可是瀏覽器對 ES module 的
# import 圖有自己的快取, 光靠 no-store 不一定重抓 —— 所以每個靜態 URL 後面都掛
# 一個 ?v=<最新 mtime>, 連 import 裡的相對路徑也是。檔案一變, URL 就變。
_STAMP = {"at": 0.0, "value": "0"}
_IMPORT = re.compile(r'((?:\bfrom|\bimport)\s*\(?\s*)(["\'])(\.{1,2}/[^"\']+?\.js)\2')


def web_stamp() -> str:
    """web/ 底下最新的 mtime, 兩秒內重複問直接給上一次的。"""
    t = now()
    if t - _STAMP["at"] < 2:
        return _STAMP["value"]
    newest = 0.0
    for path in [WEB / "index.html", WEB / "style.css", *(WEB / "dist").rglob("*.js")]:
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            pass
    _STAMP.update(at=t, value=str(int(newest)))
    return _STAMP["value"]


def stamp_imports(js: str, stamp: str) -> str:
    return _IMPORT.sub(lambda m: f"{m[1]}{m[2]}{m[3]}?v={stamp}{m[2]}", js)


class Handler(BaseHTTPRequestHandler):
    server_version = "cc-center-app"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # 平常安靜; CC_CENTER_DEBUG=1 時把每個請求印到 stderr (桌面 app 的 log 看得到)
        if os.environ.get("CC_CENTER_DEBUG"):
            super().log_message(fmt, *args)

    # -- 小工具

    def send_json(self, obj, code=200):
        self.send_blob(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8", code)

    def send_text(self, text, ctype="text/plain; charset=utf-8", code=200):
        self.send_blob(text.encode("utf-8") if isinstance(text, str) else text, ctype, code)

    def send_blob(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        if n > MAX_BODY:
            # 不讀了; 這條連線上剩下的 bytes 沒人要, 所以回完就關掉它
            self.close_connection = True
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def authed(self):
        if self.headers.get("X-CC-Token") == TOKEN:
            return True
        # EventSource 沒辦法帶 header, 所以 SSE 那條允許用 query 帶 token
        if parse_qs(urlparse(self.path).query).get("token", [None])[0] == TOKEN:
            return True
        # 回絕時 body 還沒讀; 留在 keep-alive 連線上會被當成下一個 request 的開頭
        self.close_connection = True
        self.send_json({"error": "bad token"}, 403)
        return False

    def signed_in(self):
        """第二道門: 沒登入 Supabase 就什麼都不給看。"""
        if MON.cloud.signed_in:
            return True
        self.send_json({"error": "not signed in", "auth": MON.auth_info()}, 401)
        return False

    # -- GET

    def do_GET(self):
        u = urlparse(self.path)
        path, q = u.path, parse_qs(u.query)

        if path == "/":
            html = (WEB / "index.html").read_text(encoding="utf-8")
            return self.send_text(
                html.replace("__CC_TOKEN__", TOKEN).replace("__CC_REV__", web_stamp()),
                "text/html; charset=utf-8")
        if path == "/healthz":
            return self.send_json({"ok": True, "rev": MON.rev, "pid": os.getpid()})
        if path.startswith("/static/"):
            return self.serve_static(path[len("/static/"):])
        if not path.startswith("/api/"):
            return self.send_json({"error": "not found"}, 404)
        if not self.authed():
            return

        st = read_settings()
        if path == "/api/state":
            auth = MON.auth_info()
            if not auth["signed_in"]:
                # 登入頁需要的就這些; 其他的登入以後再給
                return self.send_json({"auth": auth, "cwd": str(ROOT),
                                       "pid": os.getpid(), "started": SERVER_STARTED})
            return self.send_json({
                "auth": auth,
                "settings": st,
                "hosts": read_hosts(),
                "local_host": MON.local_host,
                "claude": bool(shutil.which("claude")),
                "cwd": str(ROOT),
                "pid": os.getpid(),
                "started": SERVER_STARTED,
                "log": MON.log[-80:],
                "report": MON.payload(st),
            })
        if not self.signed_in():
            return
        if path == "/api/report":
            return self.send_json(MON.payload(st))
        if path == "/api/claudemd":
            # 每次都真的去讀檔 (遠端一台一次 ssh), 頁面開這個分頁才會問
            hosts = MON.active_hosts(st) if st.get("remote_enabled", True) else []
            files = claudemd.collect(MON.local_host, hosts, MON.project_list(),
                                     timeout=int(st["ssh_timeout"] or 8))
            return self.send_json({"files": files, "local_host": MON.local_host})
        if path == "/api/live":
            return self.send_json(MON.live(st))
        if path == "/api/image":
            # 圖片走 JSON + base64, 不直接吐 bytes: 桌面 app 的橋只會轉文字 body
            try:
                ctype, data = images().get((q.get("id") or [""])[0])
            except ImageError as e:
                return self.send_json({"error": str(e)}, 400)
            except CloudError as e:
                return self.send_json({"error": str(e) or type(e).__name__}, 502)
            return self.send_json({"ok": True, "type": ctype,
                                   "data": base64.b64encode(data).decode("ascii")})
        if path == "/api/events":
            return self.stream()
        return self.send_json({"error": "not found"}, 404)

    def serve_static(self, rel):
        target = (WEB / rel).resolve()
        if not str(target).startswith(str(WEB.resolve())) or not target.is_file():
            return self.send_json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or "javascript" in ctype:
            ctype += "; charset=utf-8"
        body = target.read_bytes()
        if target.suffix == ".js":
            body = stamp_imports(body.decode("utf-8", "replace"), web_stamp()).encode("utf-8")
        return self.send_blob(body, ctype)

    def stream(self):
        q = BUS.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(b"retry: 2000\n\n")
            # 一接上就先講現在有幾個在等你 —— 訂閱者 (桌面 app 的狀態列) 不用等下一次變化
            self.wfile.write(f"event: attention\ndata: {json.dumps({'count': len(MON.attention)})}\n\n"
                             .encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    event, data = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                                 .encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            pass
        finally:
            BUS.unsubscribe(q)
        self.close_connection = True

    # -- POST

    def do_POST(self):
        if not self.authed():
            return
        path = urlparse(self.path).path
        body = self.read_json()

        if path.startswith("/api/auth/"):
            return self.do_auth(path[len("/api/auth/"):], body)
        if not self.signed_in():
            return

        if path == "/api/sync":
            threading.Thread(target=MON.sync_now, args=("sync requested",), daemon=True).start()
            return self.send_json({"ok": True})

        if path == "/api/settings":
            old = read_settings()
            st = write_settings(body)
            changed = {k for k in st if st[k] != old.get(k)}
            BUS.emit("settings", st)
            # 這幾個會改變資料本身, 立刻重掃
            if "disabled_hosts" in changed:
                MON.forget(set(st["disabled_hosts"]) - set(old["disabled_hosts"] or []))
                MON.push("machine selection changed")
            if changed & {"tz", "days", "date", "sidechains", "entrypoints", "include_local"}:
                MON.local_fingerprint = None
                threading.Thread(target=self.rescan_all, args=(st,), daemon=True).start()
            return self.send_json({"settings": st})

        if path == "/api/hosts":
            cur = read_hosts()
            op = body.get("op") or ("reorder" if body.get("hosts") else "")
            one = (body.get("host") or "").strip()
            if op == "add":
                hosts = cur + ([one] if one and one not in cur else [])
            elif op == "remove":
                hosts = [h for h in cur if h != one]
            elif op == "reorder":
                # 只認現在檔案裡有的, 沒提到的留在後面 —— 排序永遠不會刪掉機器
                want = [h for h in (body.get("hosts") or []) if h in cur]
                hosts = want + [h for h in cur if h not in want]
            else:
                return self.send_json({"error": "op must be add, remove or reorder"}, 400)
            gone = [h for h in cur if h not in hosts]
            write_hosts(hosts)
            if gone:
                MON.forget(gone)
            MON.push("machine list changed")
            return self.send_json({"hosts": hosts})

        if path == "/api/refresh":
            st = read_settings()
            which = body.get("hosts")
            threading.Thread(target=self.rescan_all, args=(st, which), daemon=True).start()
            if which is None:
                threading.Thread(target=MON.sync_now, args=("collect now",), daemon=True).start()
            return self.send_json({"ok": True})

        if path == "/api/probe":
            hosts = [h for h in (body.get("hosts") or []) if h]
            timeout = int(read_settings()["ssh_timeout"] or 8)
            out = {}
            ths = [threading.Thread(target=lambda h=h: out.__setitem__(h, ssh_probe(h, timeout)),
                                    daemon=True) for h in hosts]
            for t in ths:
                t.start()
            for t in ths:
                t.join()
            return self.send_json({"results": [out[h] for h in hosts if h in out]})

        if path == "/api/session/signal":
            try:
                out = MON.signal_session(body.get("host") or MON.local_host,
                                         body.get("session_id") or "",
                                         (body.get("signal") or "INT").upper(),
                                         read_settings())
            except (ValueError, OSError, subprocess.SubprocessError) as e:
                return self.send_json({"error": str(e) or type(e).__name__}, 400)
            return self.send_json({"ok": True, **out})

        if path == "/api/claudemd":
            try:
                target = claudemd.write(MON.local_host, body.get("host") or "",
                                        body.get("cwd") or None, body.get("text") or "",
                                        timeout=int(read_settings()["ssh_timeout"] or 8))
            except (OSError, subprocess.SubprocessError) as e:
                return self.send_json({"error": str(e) or type(e).__name__}, 400)
            return self.send_json({"ok": True, "path": target})

        if path == "/api/entry":
            try:
                kind = body.get("kind") or "journal"
                ident = body.get("id") or body.get("day") or ""
                if body.get("delete"):
                    warn = MON.writer.delete(kind, body.get("host") or "",
                                             body.get("cwd") or "", ident)
                    MON.bump("note")
                    return self.send_json({"ok": True, "deleted": ident, "warn": warn})
                out = MON.writer.save(
                    kind, body.get("host") or "", body.get("cwd") or "",
                    ident, body.get("text") or "", read_settings(),
                    title=body.get("title"))
                MON.bump(kind)
            except (ValueError, OSError, sqlite3.Error,
                    subprocess.SubprocessError, CloudError) as e:
                return self.send_json({"error": str(e) or type(e).__name__}, 400)
            return self.send_json({"ok": True, **out})

        if path == "/api/image":
            try:
                data = base64.b64decode(body.get("data") or "", validate=True)
            except (binascii.Error, ValueError):
                return self.send_json({"error": "the image is not valid base64"}, 400)
            try:
                name = images().save(data)
            except ImageError as e:
                return self.send_json({"error": str(e)}, 400)
            except CloudError as e:
                return self.send_json({"error": str(e) or type(e).__name__}, 502)
            return self.send_json({"ok": True, "id": name, "url": f"cc://image/{name}"})

        if path == "/api/reveal":
            target = Path(os.path.expanduser(body.get("path") or ""))
            if not target.exists():
                return self.send_json({"error": "that file does not exist"}, 404)
            try:
                if sys.platform == "darwin":
                    subprocess.run(["open", "-R", str(target)], check=False)
                elif sys.platform.startswith("linux"):
                    subprocess.run(["xdg-open", str(target.parent)], check=False)
                else:
                    os.startfile(str(target.parent))            # noqa: S606
            except OSError as e:
                return self.send_json({"error": str(e)}, 500)
            return self.send_json({"ok": True})

        if path == "/api/quit":
            threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
            return self.send_json({"ok": True})

        return self.send_json({"error": "not found"}, 404)

    def do_auth(self, op, body):
        """登入 (寄驗證碼 → 驗證)、登出。Supabase 那邊回的錯原樣給頁面看。"""
        cloud = MON.cloud
        try:
            if op == "code":
                email = cloud.request_code(body.get("email") or "")
                return self.send_json({"ok": True, "email": email})
            if op == "verify":
                cloud.verify_code(body.get("email") or "", body.get("code") or "")
                MON.sync_error = None
                # 同步完再回, 頁面一進來就有字可看 (幾百列, 一秒內)
                MON.sync_now("signed in")
                MON.journal_nagged = False
                return self.send_json({"ok": True, "auth": MON.auth_info()})
            if op == "signout":
                cloud.sign_out()
                MON.sync_error = None
                BUS.emit("update", {"reason": "signed out"})
                return self.send_json({"ok": True, "auth": MON.auth_info()})
        except CloudError as e:
            return self.send_json({"error": str(e) or type(e).__name__}, 400)
        return self.send_json({"error": "not found"}, 404)

    def rescan_all(self, st, which=None):
        if st["include_local"] and (which is None or MON.local_host in which):
            MON.local_fingerprint = None
            MON.scan_local(st, "collect now")
        remotes = MON.active_hosts(st) if which is None else \
            [h for h in which if h != MON.local_host]
        if remotes and not st.get("remote_enabled", True):
            MON.say("Remote is off, collecting locally only", "warn")
            remotes = []
        if remotes:
            MON.scan_remotes(st, remotes, "collect now")


def watch_parent(bye):
    """桌面 app 把 server 當 sidecar 帶著: app 沒了 (被砍、登出、當掉), server 也該走。

    macOS 沒有 prctl(PR_SET_PDEATHSIG), 所以殼把自己的 pid 用 CC_CENTER_PARENT_PID
    交進來, 這裡每兩秒看它還在不在。不是 sidecar 就什麼都不做。
    """
    try:
        parent = int(os.environ.get("CC_CENTER_PARENT_PID") or 0)
    except ValueError:
        parent = 0
    if not parent:
        return

    def loop():
        while True:
            threading.Event().wait(2)
            try:
                os.kill(parent, 0)
            except OSError:
                bye()
                return

    threading.Thread(target=loop, daemon=True, name="parent-watch").start()


def serve(port_hint, bind, open_browser):
    global SERVER_STARTED
    if not WEB.is_dir():
        sys.exit(f"找不到 {WEB}")
    migrate_old_names()
    # port 0 = 讓系統挑一個空的 (桌面 app 用, 它會從下面那行 ready 讀回真正的 port)
    for port in ([0] if port_hint == 0 else range(port_hint, port_hint + 20)):
        try:
            httpd = ThreadingHTTPServer((bind, port), Handler)
            break
        except OSError:
            continue
    else:
        sys.exit(f"{port_hint}~{port_hint + 19} 都被占用了")
    port = httpd.server_address[1]
    httpd.daemon_threads = True
    try:
        # 瀏覽器關掉 SSE 連線時會噴 SIGPIPE, 預設動作是直接殺掉 process
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    except (AttributeError, ValueError):
        pass

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    url = f"http://{bind}:{port}/"
    SERVER_STARTED = now()
    write_pidfile(port, url, SERVER_STARTED)

    stop_event = threading.Event()
    threading.Thread(target=monitor_loop, args=(stop_event,), daemon=True).start()

    def bye(*_):
        stop_event.set()
        clear_pidfile()
        threading.Thread(target=httpd.shutdown, daemon=True).start()
        try:
            print("\nbye", flush=True)
        except OSError:
            pass            # stdout 是殼那邊的 pipe, 殼已經不在了 —— 那正是要走的原因

    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    watch_parent(bye)

    print(f"cc-center app → {url}  (pid {os.getpid()})", flush=True)
    # 給機器讀的那行 (桌面 app 等這行才把視窗指過來)
    print("ready " + json.dumps({"port": port, "url": url, "pid": os.getpid()}), flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: open_url(url)).start()
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
