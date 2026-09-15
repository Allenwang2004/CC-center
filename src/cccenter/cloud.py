"""
The Supabase side of what you wrote.

Journal entries and notes are the half of cc-center that cannot be recomputed,
and since 2026-09 the copy that counts lives in Supabase rather than on one
machine: `supabase/schema.sql` is the table, this module is the client. It
speaks two of Supabase's HTTP surfaces and nothing else:

    /auth/v1/...     sign in with an email and a six-digit code, refresh, sign out
    /rest/v1/entries the rows themselves (PostgREST), scoped by row level security

The browser never talks to Supabase. The local server does, on its behalf,
which keeps the page's rule --- only ever `127.0.0.1` --- and this package's
rule --- standard library only --- intact. The signed-in session (refresh
token, who you are) sits in `~/.cc-center/auth.json`, mode 0600.

Configuration comes from the environment, filled in from `.env` at the repo
root: `SUPABASE_URL` and `SUPABASE_ANON_KEY` (see `.env.example`).
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

AUTH_FILE_NAME = "auth.json"
TIMEOUT = 15                  # 秒; 雲端慢就是慢, 不能把整個 server 卡住
PAGE = 1000                   # PostgREST 一次最多給這麼多列
REFRESH_MARGIN = 60           # access token 快到期就先換


class CloudError(Exception):
    """Supabase 回了錯, 或連不上。訊息是給人看的。"""


class NotSignedIn(CloudError):
    """沒有可用的登入態: 從沒登入、登出了、或 refresh token 被撤銷。"""

    def __init__(self, msg="Sign in first (Settings → Account)."):
        super().__init__(msg)


class Unreachable(CloudError):
    """網路不通或 Supabase 沒回應。快取還在, 只是這次動不了雲端。"""


def _message(body: bytes, fallback: str) -> str:
    """Supabase 的錯誤 JSON 沒有固定欄位名, 挑得到哪個就用哪個。"""
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return fallback
    if isinstance(data, dict):
        for k in ("msg", "message", "error_description", "error", "hint", "details"):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return fallback


def _filter_value(value) -> str:
    """PostgREST `eq.` 後面的值: 照字面比, 所以只做 percent-encode, 不能加引號
    (加了引號就是拿引號去比, 什麼都對不到 —— 曾經因此刪不掉雲端的列)。"""
    return urllib.parse.quote(str(value), safe="")


class Cloud:
    """一個 Supabase 專案 + 一份登入態。整個 process 共用一顆 (下面的 default())。"""

    def __init__(self, url, key, auth_file, timeout=TIMEOUT):
        self.url = (url or "").rstrip("/")
        self.key = key or ""
        self.auth_file = Path(auth_file)
        self.timeout = timeout
        self._lock = threading.RLock()
        self._session = None          # {access_token, refresh_token, expires_at, user}
        self._loaded = False

    @classmethod
    def from_env(cls):
        state = Path(os.environ.get("CC_CENTER_STATE", Path.home() / ".cc-center"))
        return cls(os.environ.get("SUPABASE_URL", ""),
                   os.environ.get("SUPABASE_ANON_KEY", ""),
                   state / AUTH_FILE_NAME)

    @property
    def configured(self) -> bool:
        return bool(self.url and self.key)

    @property
    def project(self):
        """給介面看的: 連到哪個專案 (只有 host, 不帶 key)。"""
        return urllib.parse.urlparse(self.url).netloc or None

    # -- 登入態 ------------------------------------------------------------

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        try:
            data = json.loads(self.auth_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, dict) and data.get("refresh_token"):
            self._session = data

    def _store(self, sess):
        with self._lock:
            self._session = sess
            try:
                if sess is None:
                    self.auth_file.unlink(missing_ok=True)
                    return
                self.auth_file.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.auth_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(sess, ensure_ascii=False), encoding="utf-8")
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.auth_file)
            except OSError:
                pass                    # 記憶體裡還有, 下次啟動要再登入而已

    @property
    def signed_in(self) -> bool:
        with self._lock:
            self._load()
            return self._session is not None

    def account(self):
        """{"id", "email"} 或 None。不打網路 —— 離線也答得出「你是誰」。"""
        with self._lock:
            self._load()
            if not self._session:
                return None
            u = self._session.get("user") or {}
            return {"id": u.get("id"), "email": u.get("email")}

    def forget(self):
        """把本機的登入態丟掉 (不通知 Supabase)。"""
        self._store(None)

    def _adopt(self, data):
        """GoTrue 回的 session → 我們留的那幾個欄位。"""
        if not isinstance(data, dict) or not data.get("access_token"):
            raise CloudError("Supabase did not return a session")
        expires_at = data.get("expires_at")
        if not expires_at:
            expires_at = int(time.time()) + int(data.get("expires_in") or 3600)
        user = data.get("user") or {}
        sess = {"access_token": data["access_token"],
                "refresh_token": data.get("refresh_token") or "",
                "expires_at": int(expires_at),
                "user": {"id": user.get("id"), "email": user.get("email")}}
        self._store(sess)
        return sess

    def token(self) -> str:
        """可用的 access token, 快到期就先換一把。"""
        with self._lock:
            self._load()
            if not self._session:
                raise NotSignedIn()
            if self._session.get("expires_at", 0) - REFRESH_MARGIN > time.time():
                return self._session["access_token"]
            return self._refresh()["access_token"]

    def _refresh(self):
        with self._lock:
            sess = self._session
            if not sess or not sess.get("refresh_token"):
                raise NotSignedIn()
            try:
                data = self._call("POST", "/auth/v1/token?grant_type=refresh_token",
                                  {"refresh_token": sess["refresh_token"]}, auth=False)
            except CloudError as e:
                if isinstance(e, Unreachable):
                    raise
                # refresh token 被撤銷 / 已用過: 這份登入態沒救了
                self._store(None)
                raise NotSignedIn(f"Signed out: {e}") from e
            return self._adopt(data)

    # -- 登入 / 登出 ---------------------------------------------------------

    def request_code(self, email: str):
        """寄一封信, 裡面是六位數驗證碼 (Supabase 的 Magic Link 樣板要有 {{ .Token }})。"""
        email = (email or "").strip()
        if "@" not in email:
            raise CloudError("That does not look like an email address.")
        self._call("POST", "/auth/v1/otp", {"email": email, "create_user": True}, auth=False)
        return email

    def verify_code(self, email: str, code: str):
        email = (email or "").strip()
        code = "".join(ch for ch in (code or "") if ch.isdigit())
        if not code:
            raise CloudError("Type the code from the email.")
        data = self._call("POST", "/auth/v1/verify",
                          {"type": "email", "email": email, "token": code}, auth=False)
        self._adopt(data)
        return self.account()

    def sign_out(self):
        """告訴 Supabase 這把 refresh token 作廢, 然後把本機的登入態丟掉。"""
        with self._lock:
            self._load()
            sess = self._session
        if sess:
            try:
                self._call("POST", "/auth/v1/logout", {}, auth=True, expect_json=False)
            except CloudError:
                pass                    # 連不上也一樣要登出本機
        self._store(None)

    # -- entries -------------------------------------------------------------

    def list_entries(self):
        """這個帳號的每一列 (RLS 只會給自己的), 一頁一頁收齊。"""
        out = []
        offset = 0
        while True:
            page = self._call(
                "GET",
                f"/rest/v1/entries?select=*&order=ref.desc,id.asc&limit={PAGE}&offset={offset}")
            if not isinstance(page, list):
                raise CloudError("Supabase returned something that is not a list of rows")
            out.extend(page)
            if len(page) < PAGE:
                return out
            offset += PAGE

    def upsert_entry(self, row: dict, keep_existing=False):
        """新增或覆寫一列, 回傳雲端那一列。

        keep_existing=True 是「只補缺」: 雲端已經有就不動它 (回傳 None)。
        """
        resolution = "ignore-duplicates" if keep_existing else "merge-duplicates"
        data = self._call(
            "POST", "/rest/v1/entries?on_conflict=user_id,kind,cwd,host,ref", row,
            headers={"Prefer": f"resolution={resolution},return=representation,missing=default"})
        if isinstance(data, list):
            return data[0] if data else None
        return data or None

    def delete_entry(self, kind, cwd, host, ref) -> int:
        q = (f"kind=eq.{_filter_value(kind)}&cwd=eq.{_filter_value(cwd)}"
             f"&host=eq.{_filter_value(host)}&ref=eq.{_filter_value(ref)}")
        data = self._call("DELETE", f"/rest/v1/entries?{q}",
                          headers={"Prefer": "return=representation"})
        return len(data) if isinstance(data, list) else 0

    # -- HTTP ----------------------------------------------------------------

    def _call(self, method, path, body=None, *, auth=True, headers=None, expect_json=True,
              _retry=True):
        if not self.configured:
            raise CloudError("Supabase is not configured: set SUPABASE_URL and "
                             "SUPABASE_ANON_KEY in .env (see .env.example).")
        # 沒登入態的呼叫 (寄驗證碼、換 token) 用 anon key 當 bearer, 跟 supabase-js 一樣
        hdrs = {"apikey": self.key, "Accept": "application/json",
                "Authorization": f"Bearer {self.token() if auth else self.key}"}
        if headers:
            hdrs.update(headers)
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url + path, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:   # noqa: S310
                raw = resp.read()
        except urllib.error.HTTPError as e:
            try:
                raw = e.read()
            finally:
                e.close()
            if e.code == 401 and auth and _retry:
                # token 過期或被換掉: 換一把再試一次, 還是不行就是真的登出了
                with self._lock:
                    self._refresh()
                return self._call(method, path, body, auth=auth, headers=headers,
                                  expect_json=expect_json, _retry=False)
            msg = _message(raw, f"HTTP {e.code}")
            if e.code == 401 and auth:
                raise NotSignedIn(f"Signed out: {msg}") from e
            raise CloudError(msg) from e
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            reason = getattr(e, "reason", None) or e
            raise Unreachable(f"Could not reach Supabase: {reason}") from e
        if not expect_json or not raw.strip():
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise CloudError("Supabase returned something that is not JSON") from e


_DEFAULT = None
_DEFAULT_LOCK = threading.Lock()


def default() -> Cloud:
    """整個 process 共用的那顆, 從環境變數來。"""
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = Cloud.from_env()
        return _DEFAULT
