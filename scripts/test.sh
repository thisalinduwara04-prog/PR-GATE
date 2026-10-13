#!/usr/bin/env sh
# Run the backend test suite in the "test" image (scanners + git + dev deps), with the source mounted
# and no .env loaded. The image is (re)built first; that is quick when nothing changed.
# Usage: scripts/test.sh [pytest args]
set -e
cd "$(dirname "$0")/.."
docker build -q --target test -t pr-gate-test backend >/dev/null
SRC="$(pwd -W 2>/dev/null || pwd)/backend"
MSYS_NO_PATHCONV=1 exec docker run --rm -v "$SRC:/app" pr-gate-test pytest -q "$@"
