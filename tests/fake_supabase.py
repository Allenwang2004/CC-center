"""
Just enough of Supabase to test against: the auth endpoints the client uses,
a PostgREST-shaped `entries` table, and the one private storage bucket, all in
memory, on a local port.

It answers the way the real thing does where the client depends on it --- the
shape of a session, `Prefer: resolution=...` on upsert, `limit`/`offset`
paging, `return=representation` --- and no further. Anything the client sends
that the real service would reject is rejected here too, so a test that passes
against this fake is not passing by accident.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

ANON = "anon-key-for-tests"
CODE = "123456"                     # 每封信都是這個碼
USER = {"id": "11111111-1111-1111-1111-111111111111", "email": "you@example.com"}
BUCKET = "cc-images"                # schema.sql 建的那個; 跟真的一樣, 只放行自己的資料夾
BUCKET_LIMIT = 10 * 1024 * 1024
BUCKET_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def _now():
    return datetime.now(timezone.utc).isoformat()


class State:
    def __init__(self):
        self.rows: dict[str, dict] = {}         # id -> row
        self.objects: dict[str, tuple] = {}     # "<bucket>/<path>" -> (content type, bytes)
        self.tokens: dict[str, dict] = {}       # access -> {user, expires}
        self.refresh: dict[str, dict] = {}      # refresh -> user
        self.emails: list[str] = []
        self.calls: list[tuple] = []
        self.token_ttl = 3600
        self.reject_refresh = False
        self.user = dict(USER)
        self.lock = threading.Lock()

    def session_for(self, user):
        access, refresh = f"at-{uuid.uuid4().hex}", f"rt-{uuid.uuid4().hex}"
        self.tokens[access] = {"user": user, "expires": time.time() + self.token_ttl}
        self.refresh[refresh] = user
        return {"access_token": access, "token_type": "bearer",
                "expires_in": self.token_ttl,
                "expires_at": int(time.time() + self.token_ttl),
                "refresh_token": refresh, "user": user}


class Handler(BaseHTTPRequestHandler):
    state: State

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"null") if n else None

    def _user(self):
        """Bearer access token → user, 或 None。"""
        h = self.headers.get("Authorization") or ""
        tok = h[len("Bearer "):] if h.startswith("Bearer ") else ""
        t = self.state.tokens.get(tok)
        if not t or t["expires"] < time.time():
            return None
        return t["user"]

    def _storage_key(self, rest, user):
        """/storage/v1/object/[authenticated/]<bucket>/<path> → "bucket/path", 過了 policy 才給。"""
        bucket, _, path = unquote(rest).partition("/")
        if bucket != BUCKET:
            return None, self._json({"statusCode": "404", "error": "Bucket not found",
                                     "message": "Bucket not found"}, 404)
        if path.split("/")[0] != user["id"]:
            return None, self._json({"statusCode": "403", "error": "Unauthorized",
                                     "message": "new row violates row-level security policy"}, 403)
        return f"{bucket}/{path}", None

    def do_POST(self):
        st = self.state
        u = urlparse(self.path)
        st.calls.append(("POST", u.path, dict(self.headers)))
        if self.headers.get("apikey") != ANON:
            return self._json({"message": "No API key found in request"}, 401)
        if u.path.startswith("/storage/v1/object/"):
            user = self._user()
            if not user:
                return self._json({"statusCode": "401", "message": "invalid JWT"}, 401)
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            key, err = self._storage_key(u.path[len("/storage/v1/object/"):], user)
            if err is not None:
                return err
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype not in BUCKET_TYPES:
                return self._json({"statusCode": "415", "error": "invalid_mime_type",
                                   "message": f"mime type {ctype} is not supported"}, 415)
            if len(raw) > BUCKET_LIMIT:
                return self._json({"statusCode": "413", "error": "Payload too large",
                                   "message": "The object exceeded the maximum allowed size"}, 413)
            with st.lock:
                if key in st.objects and self.headers.get("x-upsert", "false") != "true":
                    return self._json({"statusCode": "409", "error": "Duplicate",
                                       "message": "The resource already exists"}, 409)
                st.objects[key] = (ctype, raw)
            return self._json({"Key": key, "Id": str(uuid.uuid4())})
        body = self._body()
        if u.path == "/auth/v1/otp":
            st.emails.append(body["email"])
            return self._json({})
        if u.path == "/auth/v1/verify":
            if body.get("token") != CODE or body.get("type") != "email":
                return self._json({"msg": "Token has expired or is invalid"}, 403)
            return self._json(st.session_for({**st.user, "email": body["email"]}))
        if u.path == "/auth/v1/token":
            if parse_qs(u.query).get("grant_type") != ["refresh_token"]:
                return self._json({"error": "unsupported_grant_type"}, 400)
            user = st.refresh.pop(body.get("refresh_token"), None)
            if not user or st.reject_refresh:
                return self._json({"error": "invalid_grant",
                                   "error_description": "Invalid Refresh Token"}, 400)
            return self._json(st.session_for(user))
        if u.path == "/auth/v1/logout":
            if not self._user():
                return self._json({"message": "invalid JWT"}, 401)
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        if u.path == "/rest/v1/entries":
            user = self._user()
            if not user:
                return self._json({"message": "JWT expired"}, 401)
            prefer = self.headers.get("Prefer") or ""
            on_conflict = parse_qs(u.query).get("on_conflict", [""])[0].split(",")
            rows = list(body) if isinstance(body, list) else [body or {}]
            out = []
            with st.lock:
                for r in rows:
                    r = {**r}
                    r.setdefault("user_id", user["id"])
                    if r["user_id"] != user["id"]:
                        return self._json({"message": "new row violates row-level security"}, 401)
                    key = tuple(r.get(k) for k in on_conflict)
                    hit = next((x for x in st.rows.values()
                                if tuple(x.get(k) for k in on_conflict) == key), None) \
                        if on_conflict != [""] else None
                    if hit:
                        if "ignore-duplicates" in prefer:
                            continue
                        if "merge-duplicates" not in prefer:
                            return self._json({"message": "duplicate key value"}, 409)
                        hit.update({k: v for k, v in r.items() if k != "id"})
                        hit["updated_at"] = _now()
                        out.append(dict(hit))
                    else:
                        r.setdefault("id", str(uuid.uuid4()))
                        r.setdefault("title", "")
                        r.setdefault("created_at", _now())
                        r["updated_at"] = _now()
                        st.rows[r["id"]] = r
                        out.append(dict(r))
            if "return=representation" in prefer:
                return self._json(out, 201)
            self.send_response(201)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        return self._json({"message": "not found"}, 404)

    def _filters(self, query):
        """PostgREST 的 col=eq.value: 值照字面比 (引號也是字面的一部分, 跟真的一樣)。"""
        out = {}
        for k, vs in parse_qs(query, keep_blank_values=True).items():
            v = vs[0]
            if v.startswith("eq."):
                out[k] = v[3:]
        return out

    def do_GET(self):
        st = self.state
        u = urlparse(self.path)
        st.calls.append(("GET", u.path, dict(self.headers)))
        if self.headers.get("apikey") != ANON:
            return self._json({"message": "No API key found in request"}, 401)
        if u.path.startswith("/storage/v1/object/authenticated/"):
            user = self._user()
            if not user:
                return self._json({"statusCode": "401", "message": "invalid JWT"}, 401)
            key, err = self._storage_key(u.path[len("/storage/v1/object/authenticated/"):], user)
            if err is not None:
                return err
            with st.lock:
                hit = st.objects.get(key)
            if not hit:
                return self._json({"statusCode": "404", "error": "not_found",
                                   "message": "Object not found"}, 404)
            ctype, raw = hit
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return None
        if u.path != "/rest/v1/entries":
            return self._json({"message": "not found"}, 404)
        user = self._user()
        if not user:
            return self._json({"message": "JWT expired"}, 401)
        q = parse_qs(u.query)
        want = self._filters(u.query)
        with st.lock:
            rows = [dict(r) for r in st.rows.values() if r["user_id"] == user["id"]
                    and all(r.get(k) == v for k, v in want.items())]
        rows.sort(key=lambda r: (r["ref"], r["id"]))
        rows.reverse()
        offset = int(q.get("offset", ["0"])[0])
        limit = int(q.get("limit", ["1000"])[0])
        return self._json(rows[offset:offset + limit])

    def do_DELETE(self):
        st = self.state
        u = urlparse(self.path)
        st.calls.append(("DELETE", u.path, dict(self.headers)))
        user = self._user()
        if not user:
            return self._json({"message": "JWT expired"}, 401)
        want = self._filters(u.query)
        if not want:
            return self._json({"message": "refusing to delete everything"}, 400)
        with st.lock:
            gone = [r for r in st.rows.values() if r["user_id"] == user["id"]
                    and all(r.get(k) == v for k, v in want.items())]
            for r in gone:
                del st.rows[r["id"]]
        return self._json([dict(r) for r in gone])


class FakeSupabase:
    """with FakeSupabase() as fake: fake.url, fake.state"""

    def __enter__(self):
        self.state = State()
        handler = type("H", (Handler,), {"state": self.state})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, args=(0.05,), daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()

    def rows(self):
        return sorted(self.state.rows.values(), key=lambda r: r["ref"])
