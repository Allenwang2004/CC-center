"""`python -m cccenter.app` — how the background process is started."""

from __future__ import annotations

import sys

from .main import main

if __name__ == "__main__":
    sys.exit(main() or 0)
