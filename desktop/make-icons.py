"""
The app icon and the menu bar icon, drawn here so nothing binary has to be
checked in. Same mark as the page's favicon: a dark rounded square with a
green dot. Standard library only (a PNG is a zlib stream and a checksum).

    icon.png                  1024x1024, the source `tauri icon` expands into src-tauri/icons/
    src-tauri/icons/tray.png  44x44 template image (black + alpha); macOS tints it
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def png(width, height, rgba_rows) -> bytes:
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes(row) for row in rgba_rows)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def rounded_rect_alpha(x, y, w, h, r):
    """1 inside a rounded rectangle, 0 outside, smooth at the edge (4x supersampled)."""
    inside = 0
    for sy in range(4):
        for sx in range(4):
            px, py = x + (sx + 0.5) / 4, y + (sy + 0.5) / 4
            cx = min(max(px, r), w - r)
            cy = min(max(py, r), h - r)
            if (px - cx) ** 2 + (py - cy) ** 2 <= r * r:
                inside += 1
    return inside / 16


def circle_alpha(x, y, cx, cy, r):
    inside = 0
    for sy in range(4):
        for sx in range(4):
            px, py = x + (sx + 0.5) / 4, y + (sy + 0.5) / 4
            if (px - cx) ** 2 + (py - cy) ** 2 <= r * r:
                inside += 1
    return inside / 16


def app_icon(size=1024):
    ink, dot = (0x11, 0x11, 0x11), (0x4A, 0xDE, 0x80)
    pad = size * 0.06                      # macOS icons leave a margin
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            a = rounded_rect_alpha(x - pad, y - pad, size - 2 * pad, size - 2 * pad, size * 0.19)
            d = circle_alpha(x, y, size / 2, size / 2, size * 0.19)
            r, g, b = (ink[i] * (1 - d) + dot[i] * d for i in range(3))
            row += bytes((int(r), int(g), int(b), int(255 * a)))
        rows.append(row)
    return png(size, size, rows)


def tray_icon(size=44):
    """A ring with a dot: reads as 'watching' at 22pt, works as a template image."""
    c, r_out, r_in, r_dot = size / 2, size * 0.42, size * 0.30, size * 0.15
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            a = (circle_alpha(x, y, c, c, r_out) - circle_alpha(x, y, c, c, r_in)
                 + circle_alpha(x, y, c, c, r_dot))
            row += bytes((0, 0, 0, int(255 * max(0.0, min(1.0, a)))))
        rows.append(row)
    return png(size, size, rows)


if __name__ == "__main__":
    (HERE / "icon.png").write_bytes(app_icon())
    (HERE / "src-tauri" / "icons").mkdir(parents=True, exist_ok=True)
    (HERE / "src-tauri" / "icons" / "tray.png").write_bytes(tray_icon())
    print("icon.png and src-tauri/icons/tray.png written")
