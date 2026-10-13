# PR-Gate local setup guide (for AI coding agents and humans)

> **Teammates:** after cloning, tell your AI coding agent (Claude Code, Copilot, Cursor, etc.):
> *"Read SETUP.md and set up this project on my machine."*
> The agent does the steps marked 🤖, and stops and tells you exactly what to do at steps marked 🧑.

For what the project is and how it is built, see [README.md](README.md).

---

## Rules for the AI agent

1. **Never ask the human to paste a secret into the chat**, and never print secret values. Secrets go into `.env` only, and the human types them there. To check `.env`, report only whether each key is *set* or *empty*; the commands are in step B6.
2. **Never commit `.env`.** It is gitignored; keep it that way.
3. **Ask before any action that changes something outside this machine:** pushing to a GitHub repo, opening PRs, or creating GitHub resources. Local actions (building images, running tests, starting containers) don't need asking.
4. Work step by step. After each step, run its **✅ Verify** command, and continue only when it passes. If it fails, use [Troubleshooting](#troubleshooting) before moving on.
5. At each 🧑 step, give the human the exact clicks and values from this file, then wait for them to say it's done.
6. Detect the OS and shell first. On **Windows**, prefer PowerShell for commands shown as PowerShell. `scripts/test.sh` and `*.sh` files need **Git Bash** (included with Git for Windows).

---

## Step 0: decide the goal 🤖

Ask the human which they want:

- **Path A, develop and test only.** Build the images, run the test suite and the dashboard build. **No accounts or tokens needed.** About 10 minutes.
- **Path B, run the full pipeline.** Real GitHub PRs get reviewed by PR-Gate on this machine. Needs a Claude Pro/Max subscription, a GitHub account and a free ngrok account. About 30–45 minutes, much of it the human's clicks.

Each person who runs Path B needs **their own** ngrok domain, demo repository and webhook. A webhook delivers to one machine, so teammates can't share them.

---

## Step 1: prerequisites 🤖

Check each tool. Install anything missing, after asking the human, or tell them how.

| Tool | Check | Needed for | Install |
|---|---|---|---|
| Git | `git --version` | everything | https://git-scm.com (on Windows this includes Git Bash) |
| Docker Desktop (running) | `docker info` (must not error) | everything | https://www.docker.com/products/docker-desktop. On Windows it uses WSL2; follow the installer |
| Docker Compose v2 | `docker compose version` | everything | included with Docker Desktop |
| Node.js 20+ and npm | `node --version` and `npm --version` | dashboard | https://nodejs.org (LTS) |
| Claude Code CLI | `claude --version` | Path B only, to create the token | `npm install -g @anthropic-ai/claude-code` |

Python is **not** needed on the host: the backend and its tests run in Docker.

✅ **Verify:** every check above prints a version, and `docker info` shows a `Server Version`.

If `docker info` fails, Docker Desktop isn't running. Ask the human to start it, or start it yourself, then wait until `docker info` succeeds. It can take a minute.

---

## Path A: develop and test only

### A1. Build the images and run the backend tests 🤖
From the repository root, in **Git Bash** on Windows or any shell on macOS/Linux:
```bash
docker compose build
scripts/test.sh
```
The first build downloads scanners, Node.js and the Claude CLI, and takes 5–10 minutes. `scripts/test.sh` builds the `test` image, then runs pytest in it with the source mounted.

✅ **Verify:** the last line is `N passed` (about 160) with no failures.

### A2. Build the dashboard 🤖
```bash
cd dashboard
npm ci
npm run build
```
✅ **Verify:** the output ends with `✓ built in …`.

That completes Path A. To see the dashboard against a backend, continue with Path B. The dashboard needs a running backend for data.

---

## Path B: run the full pipeline

### B1. Create `.env` and the webhook secret 🤖
From the repository root, copy the template, but only if `.env` doesn't exist yet. Never overwrite an existing `.env`.
```powershell
# Windows PowerShell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```
```bash
# macOS / Linux / Git Bash
[ -f .env ] || cp .env.example .env
```
Generate a random webhook secret and write it into `.env` **without printing it**:
```bash
# Git Bash / macOS / Linux
S=$(openssl rand -hex 32); sed -i.bak "s/^GITHUB_WEBHOOK_SECRET=.*/GITHUB_WEBHOOK_SECRET=$S/" .env && rm -f .env.bak; unset S
```
```powershell
# Windows PowerShell (works on 5.1 and 7)
$b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); $s = -join ($b | ForEach-Object { $_.ToString('x2') })
(Get-Content .env) -replace '^GITHUB_WEBHOOK_SECRET=.*', "GITHUB_WEBHOOK_SECRET=$s" | Set-Content -Encoding ascii .env; Remove-Variable s, b
```
✅ **Verify:** step B6 reports `GITHUB_WEBHOOK_SECRET` as set.

### B2. Claude token 🧑
Tell the human:
1. Open a terminal and run `claude setup-token`.
2. Sign in with your **Claude Pro or Max** account in the browser window that opens.
3. Copy the printed token, which starts with `sk-ant-oat01-`.
4. Open `.env` in the repository and set `CLAUDE_CODE_OAUTH_TOKEN=<token>`: one line, no quotes, no spaces. Save the file.

### B3. ngrok account and domain 🧑
Tell the human:
1. Sign up at https://dashboard.ngrok.com/signup. The free plan is enough, and no card is needed.
2. Go to **Getting Started → Your Authtoken**, copy it, and set `NGROK_AUTHTOKEN=<token>` in `.env`.
3. Go to **Domains** (under *Universal Gateway*). Free accounts already have one "dev domain", or you can click **+ New Domain**. Copy the host name only, e.g. `something-random.ngrok-free.dev`, with **no** `https://`.
4. Set `NGROK_DOMAIN=<host name>` in `.env` and save.

The ngrok agent is **not** installed on the host; it runs in Docker.

### B4. Demo repository on GitHub 🧑 then 🤖
The human:
1. On github.com, click **+ → New repository**.
2. Name it `pr-gate-demo`. Private is recommended.
3. Leave *README*, *.gitignore* and *license* **all unticked**; the repository must be empty.
4. Click **Create repository** and give the agent the HTTPS URL, e.g. `https://github.com/<user>/pr-gate-demo.git`.

The agent, **after confirming with the human** (this pushes to GitHub), runs from the repository root:
```powershell
# Windows PowerShell
powershell -ExecutionPolicy Bypass -File demo-repo\scripts\publish-demo.ps1 -RepoUrl <URL>
```
```bash
# macOS / Linux / Git Bash
demo-repo/scripts/publish-demo.sh <URL>
```
This creates a `pr-gate-demo` folder **next to** the `pr-gate` clone and pushes the branches `main`, `vuln-pr` and `hostile-pr`. **Do not open any pull requests yet.**

✅ **Verify:** `git -C ../pr-gate-demo ls-remote --heads origin` lists `main`, `vuln-pr` and `hostile-pr`.

### B5. GitHub fine-grained token 🧑
Tell the human:
1. Go to GitHub → avatar → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Name it `pr-gate`. Expiration: 30–90 days. Resource owner: **yourself**.
3. Repository access: **Only select repositories** → `pr-gate-demo`.
4. Under **Repository permissions**, set:
   - **Contents:** Read-only
   - **Pull requests:** Read and write
   - **Commit statuses:** Read and write
   - Metadata: Read-only (added automatically)
5. Click **Generate token**, copy it (it is shown only once), and set `GITHUB_TOKEN=<token>` in `.env`.
6. Set `WATCHED_REPOS=<user>/pr-gate-demo` in `.env`, exactly as it appears in the repo URL. Save.

### B6. Check `.env` (without revealing values) 🤖
```bash
# Git Bash / macOS / Linux
awk -F= '/^[A-Z_]+=/{v=substr($0,index($0,"=")+1); printf "%-26s %s\n", $1, (length(v)>0 ? "set" : "EMPTY")}' .env
```
```powershell
# Windows PowerShell
Get-Content .env | Where-Object { $_ -match '^[A-Z_]+=' } | ForEach-Object { $k,$v = $_ -split '=',2; '{0,-26} {1}' -f $k, $(if ($v) {'set'} else {'EMPTY'}) }
```
✅ **Verify:** all of `GITHUB_TOKEN`, `GITHUB_WEBHOOK_SECRET`, `NGROK_AUTHTOKEN`, `NGROK_DOMAIN`, `WATCHED_REPOS` and `CLAUDE_CODE_OAUTH_TOKEN` are `set`. Also check that `CLAUDE_CODE_OAUTH_TOKEN` starts with `sk-ant-oat` without printing it:
```bash
grep -q '^CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat' .env && echo "prefix ok" || echo "WRONG PREFIX"
```

### B7. Start the stack 🤖
```bash
docker compose up -d --build
docker compose ps
```
✅ **Verify:**
- `docker compose ps` shows **backend, scanner, agent and ngrok** all `running`. ngrok must not be "restarting".
- `curl http://localhost:8000/api/health` returns `{"status":"ok"}`.
- `curl https://<NGROK_DOMAIN>/api/runs` returns `Not found` (ngrok blocks everything except the webhook path).
- `curl -s -o /dev/null -w "%{http_code}" https://<NGROK_DOMAIN>/webhook/github` returns `405`, which means the request reached the backend.
- The Claude token works from inside the agent container:
  ```bash
  docker compose exec -T agent sh -c 'cd /tmp && echo "Reply with exactly: pong" | claude -p --restricted --tools "" --permission-mode dontAsk --strict-mcp-config --no-session-persistence --max-turns 1 --output-format json' | grep -o '"result":"[^"]*"'
  ```
  This should print `"result":"pong"`. In Git Bash on Windows, prefix `docker` commands that contain `/paths` with `MSYS_NO_PATHCONV=1`.

### B8. Register the GitHub webhook 🧑
Tell the human to open `https://github.com/<user>/pr-gate-demo/settings/hooks` → **Add webhook**, and fill in:
- **Payload URL:** `https://<NGROK_DOMAIN>/webhook/github`
- **Content type:** `application/json` (not the default form type)
- **Secret:** the value of `GITHUB_WEBHOOK_SECRET` from `.env`, copied exactly
- **SSL verification:** Enable
- **Which events?:** *Let me select individual events*. Untick **Pushes**, tick **Pull requests** only.
- **Active:** ticked → **Add webhook**

✅ **Verify (agent):** `docker compose logs backend | grep "POST /webhook/github"` shows a `200 OK`, which is GitHub's ping. The human can also check the webhook's **Recent Deliveries** tab for a green 200.

### B9. Start the dashboard 🤖
```bash
cd dashboard
npm ci
npm run dev
```
Keep it running. Tell the human to open **http://localhost:5173**.

✅ **Verify:** `curl -s -o /dev/null -w "%{http_code}" http://localhost:5173` returns `200`.

### B10. First real review 🧑 + 🤖
The human opens a PR at `https://github.com/<user>/pr-gate-demo/compare/main...vuln-pr` → **Create pull request**.

✅ **Verify (agent):** poll until the newest run finishes. It usually takes 1–3 minutes:
```bash
curl -s http://localhost:8000/api/runs | head -c 400
```
The newest run should go `queued → running → completed`. Then confirm with the human:
- one **PR-Gate security review** appears on the PR, with inline comments,
- the PR's head commit shows a `pr-gate` check with state **success**,
- the dashboard's run page shows the findings and a **Download tool outputs (.zip)** button.

If the run ends `failed`, read its `error` field in the dashboard or with `curl http://localhost:8000/api/runs/<id>`, then see Troubleshooting.

Optional: also open `main...hostile-pr`, the prompt-injection test described in [demo-repo/README.md](demo-repo/README.md).

---

## Daily use

| Task | Command |
|---|---|
| Start everything | `docker compose up -d`, then `cd dashboard && npm run dev` |
| Stop everything | `docker compose down` (the data volume keeps runs and history) |
| Follow logs | `docker compose logs -f backend scanner agent` |
| After pulling new code | `docker compose up -d --build` |
| Run the tests | `scripts/test.sh` (Git Bash on Windows) |
| Re-run a PR | push a commit to its branch. GitHub's *Redeliver* button is ignored on purpose (duplicate guard) |

Missed webhooks while the stack was off are picked up automatically: on startup, PR-Gate reviews open PRs whose latest commit was never reviewed.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `docker info` / `docker compose` errors about a pipe or socket | Docker Desktop isn't running. Start it and wait until `docker info` works |
| Webhook delivery shows **401** | The secret in GitHub ≠ `GITHUB_WEBHOOK_SECRET`. Fix one of them, then run `docker compose up -d` |
| Delivery 200 but `{"ignored": "repository not watched"}` | `WATCHED_REPOS` doesn't match `owner/name`. Fix `.env`, then `docker compose up -d` |
| Run failed: `git fetch failed` | The token can't see the repo. Check its *Repository access* and *Contents: Read-only* |
| Run failed: `GitHub review POST failed: 403` | The token needs *Pull requests: Read and write* |
| No `pr-gate` check on the PR; backend log says `status POST failed: 403` | The token needs *Commit statuses: Read and write* (the review still works) |
| Run failed with `SELF_SIGNED_CERT_IN_CHAIN` | Antivirus HTTPS scanning (e.g. Kaspersky *Encrypted connections scanning*) intercepts `api.anthropic.com`. The human must add `api.anthropic.com` to the antivirus' trusted addresses. To check, run `docker compose exec -T agent sh -c 'echo \| openssl s_client -connect api.anthropic.com:443 2>/dev/null \| openssl x509 -noout -issuer'`. An antivirus name in the issuer means it is still intercepting |
| Run failed: `claude run did not succeed … api_error` | Claude API hiccup or rate limit. PR-Gate retries 3 times; push a commit to try again later. If it persists, regenerate the token with `claude setup-token` |
| Run failed: `CLAUDE_CODE_OAUTH_TOKEN is not set` | Set it in `.env`, then `docker compose up -d` |
| ngrok container keeps restarting | Check `docker compose logs ngrok`. Usually `NGROK_AUTHTOKEN` or `NGROK_DOMAIN` is wrong (domain must have no `https://`) |
| `https://<domain>/…` returns an ngrok "endpoint offline" page | The ngrok container isn't running; see the row above |
| Port 8000 or 5173 already in use | Stop the other program, or change the left side of `127.0.0.1:8000:8000` in `docker-compose.yml`. The dashboard expects port 5173 for CORS |
| Git Bash turns `/tmp` or `/app` into `C:/Program Files/Git/...` | Prefix the command with `MSYS_NO_PATHCONV=1` |
| `scripts/test.sh: Permission denied` | Run `sh scripts/test.sh` instead |
| Dashboard shows "Could not reach the PR-Gate API" | The backend is down: run `docker compose up -d`, then `curl http://localhost:8000/api/health` |
