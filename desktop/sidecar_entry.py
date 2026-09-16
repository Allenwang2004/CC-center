"""
The sidecar's entry point: `cc-center-server`, the same command line as
`bin/cc-center-app`, frozen with PyInstaller so the app needs no Python on the
machine it is installed on.

One thing a frozen build has to do for itself: certificates. Homebrew's Python
finds its CA bundle through Homebrew, which the customer's machine does not
have, so without this Supabase would fail TLS verification. certifi is bundled
at build time and pointed to here unless the environment already says otherwise.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False) and not os.environ.get("SSL_CERT_FILE"):
    bundled = Path(getattr(sys, "_MEIPASS", "")) / "certifi" / "cacert.pem"
    if bundled.is_file():
        os.environ["SSL_CERT_FILE"] = str(bundled)

from cccenter.app.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main() or 0)
