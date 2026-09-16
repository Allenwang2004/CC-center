#!/bin/sh
# Run a command with the repository's .env exported. The Rust shell reads
# SUPABASE_URL / SUPABASE_ANON_KEY at compile time (option_env!) and bakes
# them into the app, so the build has to see them; the sidecar it starts
# inherits the rest (CC_HOSTS, CC_TZ...) the same way the CLI would.
set -e
cd "$(dirname "$0")"
if [ -f ../.env ]; then
  set -a
  . ../.env
  set +a
fi
exec "$@"
