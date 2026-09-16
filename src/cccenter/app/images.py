"""
The pictures in a journal entry.

A screenshot pasted into the journal goes to Supabase Storage, into a private
bucket (`supabase/schema.sql`: `cc-images`) whose policy lets an account reach
its own folder and nothing else. The page never sees the bucket: it hands the
bytes to the local server (`/api/image`), the server puts them up with your
token, and the entry's Markdown refers to the picture as

    ![what it shows](cc://image/<name>)

which the page resolves back through the server. That keeps the two rules
that hold everywhere else --- the browser talks only to 127.0.0.1, and what
you wrote is scoped to your account --- and means the same entry shows the
same picture on every machine you sign in on.

Every picture that passes through is also kept under `~/.cc-center/images/`,
so an entry you have already looked at opens offline, and a paste shows up
without a round trip.
"""

from __future__ import annotations

import os
import re
import secrets
from datetime import datetime
from pathlib import Path

from .. import cloud as cloudmod

MAX_BYTES = 10 * 1024 * 1024
TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
NAME = re.compile(r"\A[0-9]{8}-[0-9]{6}-[0-9a-f]{8}\.(png|jpg|gif|webp)\Z")
IMAGE_DIR = "images"


class ImageError(ValueError):
    """Something about the picture itself: too big, not a type the bucket takes."""


def _sniff(data: bytes) -> str | None:
    """實際的檔頭, 不信瀏覽器報的型別。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class Images:
    """上傳與取回。cloud 沒給就用環境變數那顆; cache_dir 是本機留一份的地方。"""

    def __init__(self, cache_dir, cloud=None):
        self.cloud = cloud if cloud is not None else cloudmod.default()
        self.cache_dir = Path(cache_dir)

    def save(self, data: bytes) -> str:
        """一張圖上雲, 回傳它的名字 (Markdown 裡 cc://image/ 後面那段)。"""
        if not data:
            raise ImageError("The image is empty.")
        if len(data) > MAX_BYTES:
            raise ImageError(f"Images are limited to {MAX_BYTES // (1024 * 1024)} MB.")
        ctype = _sniff(data)
        if not ctype:
            raise ImageError("Only PNG, JPEG, GIF and WebP images can be added.")
        name = (f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"
                f".{TYPES[ctype]}")
        self.cloud.upload_image(name, data, ctype)
        self._keep(name, data)
        return name

    def get(self, name: str):
        """(content type, bytes)。本機有就本機的, 沒有才去雲端拿 (拿到就留一份)。"""
        if not NAME.match(name or ""):
            raise ImageError("That is not the name of an image.")
        local = self.cache_dir / name
        try:
            data = local.read_bytes()
            return _sniff(data) or "application/octet-stream", data
        except OSError:
            pass
        ctype, data = self.cloud.download_image(name)
        self._keep(name, data)
        return _sniff(data) or ctype, data

    def _keep(self, name: str, data: bytes) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_dir / f".{name}.tmp"
            tmp.write_bytes(data)
            os.replace(tmp, self.cache_dir / name)
        except OSError:
            pass                    # 快取而已; 雲端有就好
