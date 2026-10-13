#!/usr/bin/env sh
# Sends an unsigned (forged) pull_request webhook to the public ngrok URL. Expected: HTTP 401.
# Usage: NGROK_DOMAIN=your-domain.ngrok-free.app sh demo-repo/scripts/forged_post.sh
set -eu
: "${NGROK_DOMAIN:?set NGROK_DOMAIN}"
curl -s -o /dev/null -w "forged webhook -> HTTP %{http_code} (expected 401)\n" \
  -X POST "https://${NGROK_DOMAIN}/webhook/github" \
  -H "Content-Type: application/json" -H "X-GitHub-Event: pull_request" \
  -H "X-Hub-Signature-256: sha256=0000000000000000000000000000000000000000000000000000000000000000" \
  -d '{"action":"opened","repository":{"full_name":"attacker/repo"},"pull_request":{"number":1,"head":{"sha":"x","ref":"x"},"base":{"sha":"y","ref":"main"}}}'
curl -s -o /dev/null -w "dashboard API via ngrok -> HTTP %{http_code} (expected 404)\n" "https://${NGROK_DOMAIN}/api/runs"
