#!/usr/bin/env bash
# Builds the pr-gate-demo git repository from demo-repo/ and pushes main, vuln-pr and hostile-pr.
# macOS/Linux/Git Bash equivalent of publish-demo.ps1.
#
# Usage: demo-repo/scripts/publish-demo.sh https://github.com/<you>/pr-gate-demo.git [target-dir]
# The GitHub repository must already exist and be EMPTY. No pull requests are opened: open them only
# after the webhook is registered (see SETUP.md).
set -euo pipefail

REPO_URL="${1:?usage: publish-demo.sh <repo-url> [target-dir]}"
DEMO="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${2:-$(cd "$DEMO/../.." && pwd)/pr-gate-demo}"

if [ -e "$TARGET" ]; then
  echo "$TARGET already exists. Delete it or pass a different target dir." >&2
  exit 1
fi

overlay() { cp -R "$1"/. "$TARGET"/; }   # "/." copies dot-files such as .claude and .semgrepignore

mkdir -p "$TARGET"
overlay "$DEMO/base"
cd "$TARGET"

git init -q -b main
git add -A
git commit -q -m "Initial clean demo app"
git remote add origin "$REPO_URL"
git push -u origin main

git checkout -q -b vuln-pr
overlay "$DEMO/prs/vuln"
git add -A
git commit -q -m "Add user search, admin import and payment client"
git push -u origin vuln-pr

git checkout -q main
git checkout -q -b hostile-pr
overlay "$DEMO/prs/hostile"
git add -A
git commit -q -m "Add calculator helper and project settings"
echo "Files in the hostile commit:"
git show --stat --oneline HEAD | cat
git push -u origin hostile-pr

git checkout -q main
echo
echo "Done. Branches pushed: main, vuln-pr, hostile-pr. Do NOT open PRs until the webhook is registered."
