"""Three things too small to have a home of their own."""

from __future__ import annotations

import time
from datetime import datetime


def now() -> float:
    """Wall clock seconds. One name for it, so tests can see every use."""
    return time.time()


def parse_iso(v):
    """ISO string → epoch seconds, or None if it is not a time."""
    try:
        return datetime.fromisoformat(v).timestamp() if v else None
    except (TypeError, ValueError):
        return None


def shell_quote(s) -> str:
    """Single-quote a value for a remote shell. Everything sent over ssh goes
    through this --- a project path can contain a space, and worse."""
    return "'" + str(s).replace("'", "'\\''") + "'"
