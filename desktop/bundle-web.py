"""
web/ → desktop/dist/, the page the app's window loads.

The server serves the same files under /static/ and injects a token into
index.html; inside the app there is no server in front of the page, so the
paths become relative, the token placeholder is emptied (the Rust shell holds
the real one) and the revision stamp is the build time. web/ itself is not
touched: one front end, two ways of serving it.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB = HERE.parent / "web"
OUT = HERE / "dist"


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    static = OUT / "static"
    static.mkdir(parents=True)
    shutil.copy2(WEB / "style.css", static / "style.css")
    shutil.copytree(WEB / "dist", static / "dist",
                    ignore=shutil.ignore_patterns("*.map", "*.tsbuildinfo"))
    html = (WEB / "index.html").read_text(encoding="utf-8")
    html = (html.replace('href="/static/', 'href="static/')
                .replace('src="/static/', 'src="static/')
                .replace("__CC_TOKEN__", "")
                .replace("__CC_REV__", str(int(time.time()))))
    (OUT / "index.html").write_text(html, encoding="utf-8")
    n = sum(1 for _ in OUT.rglob("*") if _.is_file())
    print(f"web → {OUT} ({n} files)")


if __name__ == "__main__":
    main()
