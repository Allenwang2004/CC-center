#!/bin/sh
# Put the freshly built app into /Applications, replacing what is there.
# A running copy is quit first (which also stops its sidecar), so the swap
# never happens under a live process.
set -e
cd "$(dirname "$0")"
SRC=src-tauri/target/release/bundle/macos/cc-center.app
DEST=/Applications/cc-center.app
[ -d "$SRC" ] || { echo "no build yet: run npm run build first" >&2; exit 1; }
if pgrep -fq "cc-center.app/Contents/MacOS/cc-center-desktop"; then
  osascript -e 'tell application "cc-center" to quit' >/dev/null 2>&1 || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    pgrep -fq "cc-center.app/Contents/MacOS/cc-center-desktop" || break
    sleep 0.5
  done
fi
rm -rf "$DEST"
cp -R "$SRC" "$DEST"
echo "installed → $DEST ($(du -sh "$DEST" | cut -f1))"
