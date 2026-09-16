#!/usr/bin/env bash
# Freeze the Python server into one binary for Tauri to carry as a sidecar:
#
#   desktop/src-tauri/binaries/cc-center-server-<target triple>
#
# Build-time only: a throwaway venv with PyInstaller and certifi. The app that
# comes out needs no Python. scanner.py and web/ are copied into the bundle at
# the same relative places the code expects (see cccenter/app/paths.py).
set -euo pipefail
cd "$(dirname "$0")"
ROOT=$(cd .. && pwd)
VENV=.venv-build
PY=${PYTHON:-python3}

[ -d "$VENV" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip pyinstaller certifi

TRIPLE=$(rustc -vV | sed -n 's/^host: //p')
CERT=$("$VENV/bin/python" -c 'import certifi; print(certifi.where())')
mkdir -p src-tauri/binaries

"$VENV/bin/pyinstaller" --noconfirm --clean --onefile --console \
  --name cc-center-server \
  --distpath src-tauri/binaries --workpath build --specpath build \
  --paths "$ROOT/src" \
  --add-data "$ROOT/src/cccenter/scanner.py:cccenter" \
  --add-data "$ROOT/web/index.html:web" \
  --add-data "$ROOT/web/style.css:web" \
  --add-data "$ROOT/web/dist:web/dist" \
  --add-data "$CERT:certifi" \
  sidecar_entry.py

mv -f "src-tauri/binaries/cc-center-server" "src-tauri/binaries/cc-center-server-$TRIPLE"
echo "sidecar → src-tauri/binaries/cc-center-server-$TRIPLE"
"src-tauri/binaries/cc-center-server-$TRIPLE" --help >/dev/null && echo "sidecar runs"
