#!/usr/bin/env bash
# Start the Files Explorer web app.  HOST=0.0.0.0 PORT=8300 ./scripts/start.sh
# (HOST=0.0.0.0 makes it reachable from other machines - the web UI has no login.)
set -euo pipefail
cd "$(dirname "$0")/.."
[ -x .venv/bin/python ] || { echo "Run ./scripts/setup.sh first."; exit 1; }
exec .venv/bin/python -m filerag web --host "${HOST:-127.0.0.1}" --port "${PORT:-8200}"
