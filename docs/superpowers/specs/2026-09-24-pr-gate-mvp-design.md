# PR-Gate MVP (Part A): Design and Task Breakdown

## Context
`C:\ISP\IMPLEMENTATION_PLAN.md` describes PR-Gate. A GitHub webhook comes in through ngrok. The backend runs Semgrep, Gitleaks and OSV-Scanner on the PR checkout, and Claude labels each finding confirmed / false_positive / unclear. One advisory review is posted with only the confirmed findings, and a React dashboard shows everything.

The repo (`C:\ISP\pr-gate`) has only infrastructure so far: `docker-compose.yml`, `ngrok/policy.yml`, `.env.example`, `.gitignore`, and `backend/Dockerfile` (tools installed, CMD is `sleep infinity`). This cycle builds **Part A (MVP)**. Part B is out of scope.

**Decisions made while brainstorming**
- **Scope:** Part A MVP only.
- **Execution:** inline, TDD, one commit per task, on branch `mvp`, merged to `main` at the end.
- **Demo repo:** `demo-repo/` is a folder in pr-gate. It is a Python/Flask project that the user copies into a separate GitHub repo.
- **Claude credential:** `CLAUDE_CODE_OAUTH_TOKEN` only.
- **Tests:** there is no Python on the host, so tests run in the container with `docker compose run --rm backend pytest`. Dev dependencies are installed in the image.
- **Tool versions:** the Dockerfile pins osv-scanner **2.6.0**, so the code uses v2 syntax: `osv-scanner scan source -r . --format sarif --output <file>`. The plan's `scan -r` is v1 syntax. Claude flags are checked with `claude --help` inside the container. In particular, `--max-turns` is missing from the local 2.1.217 help, and the code drops it if 2.1.278 doesn't support it either.

## Design (additions beyond IMPLEMENTATION_PLAN.md)
- **Secret isolation:** scanners and `claude` are started with a **minimal env** (PATH, HOME, and `CLAUDE_CODE_OAUTH_TOKEN` for claude only). `GITHUB_TOKEN` and `GITHUB_WEBHOOK_SECRET` are never passed to child processes.
- **Testable git:** `workspace.py` takes a `remote_url` (the default is `https://github.com/{repo}.git`). Tests use a local bare repo with a `refs/pull/1/head` ref, so no network is needed.
- **Semgrep offline and deterministic:** local `config/semgrep-rules.yml` (Python SQLi via string formatting/f-string into `execute`, `subprocess` `shell=True`, `eval`/`exec`, `pickle.loads`, `yaml.load` without SafeLoader, Flask `debug=True`), `--metrics=off`, `SEMGREP_ENABLE_VERSION_CHECK=0`.
- **Masking:** gitleaks runs with `--redact`. As well as that, `publish.py` replaces the message of every gitleaks finding with `Secret detected (<rule_id>) — value redacted`.
- **Host guard:** `/api/*` only accepts a `Host` of `localhost[:8000]` or `127.0.0.1[:8000]`. Requests through ngrok have the ngrok Host, so they get 403 even if the policy is bypassed. `GET /api/health` is added for the smoke checks.
- **Worker:** started in the FastAPI `lifespan`. It uses one `asyncio.Queue`, and the blocking pipeline steps run in `asyncio.to_thread`.
- **Dependencies:** fastapi, uvicorn[standard], pydantic, pydantic-settings, httpx, unidiff, jsonschema. Dev: pytest, pytest-asyncio.

## Task breakdown
Legend: **[CODE]** is implemented by Claude (test first, `docker compose run --rm backend pytest`, then commit). **[MANUAL]** is done by the user, with full steps given below.

| # | Task | Plan steps | Key files |
|---|---|---|---|
| T0 | [CODE] Create branch `mvp`. Write the spec to `docs/superpowers/specs/2026-09-24-pr-gate-mvp-design.md` and commit it | — | docs/ |
| M1 | **[MANUAL]** Accounts, tokens, GitHub demo repo, ngrok domain, fill in `.env` | 1 | `.env` |
| T1 | [CODE] Backend skeleton: `pyproject.toml`, `app/__init__`, `config.py` (pydantic-settings), `main.py` with `/api/health`. Dockerfile: COPY + `pip install .[dev]` + CMD `uvicorn app.main:app --host 0.0.0.0 --port 8000`. First smoke test | 4 | backend/pyproject.toml, app/config.py, Dockerfile |
| T2 | [CODE] Contracts + DB: Pydantic models, `classification.schema.json`, SQLite WAL with the 4 tables, parameterised helpers | 6–7 | app/contracts/*, app/db.py |
| T3 | [CODE] HTTP layer: Host middleware, CORS (localhost:5173, GET), webhook (size cap, HMAC, ping, event/action/repo filter, pr_latest upsert, run insert, 202) | 8–9 | app/main.py, app/webhook.py |
| T4 | [CODE] Worker + run skeleton: queue, re-queue of `queued` runs on startup, status transitions, supersede check, `try/finally` rmtree | 10, 18, 24 | app/worker.py, app/pipeline/run.py |
| T5 | [CODE] Workspace + diff: init/fetch with extraheader, checkout, sanitise + chmod, merge-base, unified=0 diff → `{path:set(lines)}` | 15–17 | app/pipeline/workspace.py, diff.py |
| T6 | [CODE] Scan + normalise: 3 tools, timeouts, `tool_reports`, SARIF parser + severity tables, registry, dedup, semgrep rules | 19–20 | app/pipeline/scan.py, app/normalizers/*, config/semgrep-rules.yml |
| T7 | [CODE] Verify: prompt template, claude subprocess with timeout/kill, schema + ID-coverage validation, fake-claude test script | 21–23 | app/pipeline/verify.py, app/prompts/verify.md, tests/fake_claude.py |
| T8 | [CODE] Publish: build the review payload (inline/body/unresolved, severity threshold, masking, event=COMMENT), POST via httpx, store review_id | 25 | app/pipeline/publish.py |
| T9 | [CODE] Wire the full pipeline in `run.py`. Integration test with a local git remote, fake claude and a mocked GitHub, including "workspace removed after failure" | 18, 24 | app/pipeline/run.py |
| T10 | [CODE] Dashboard API: `/api/runs`, `/api/runs/{id}`, `/api/stats/tools` | 26 | app/api.py |
| T11 | [CODE] React dashboard: Vite React TS, Router, TanStack Query; Runs, Run detail (filters, expandable justification, 3s polling), Tool stats. Check with `npm run build` and the preview browser | 27 | dashboard/ |
| T12 | [CODE] `demo-repo/` layout: `base/` (a clean Flask app with `requirements.txt`), `prs/vuln/` (overlay: SQLi route, hardcoded fake key, old vulnerable `requirements.txt` pin), `prs/hostile/` (overlay: `.claude/settings.json`, `CLAUDE.md`, a source comment with injected instructions), `scripts/forged_post.sh`. The fake key is checked with gitleaks in the container, so it's detected but isn't a real credential | 29 | demo-repo/ |
| M-GH | **[MANUAL]** Publish the sample repo to GitHub: create `pr-gate-demo`, push `base/` to `main`, push the `vuln-pr` and `hostile-pr` branches (no PRs yet), create the PAT for it, set `WATCHED_REPOS` | 1.3, 29 | `.env` |
| T13 | [CODE] README: setup, ngrok, webhook, run commands, limitations | 30 | README.md |
| M2 | **[MANUAL]** `docker compose up -d --build`, register the webhook, confirm ping = 200 | 13 | — |
| M3 | **[MANUAL + Claude assists]** Open the PRs from the pushed branches, run the end-to-end checklist | Verification | — |
| T14 | [CODE] Fix whatever E2E turns up, finish the branch (merge `mvp` → `main`) | — | — |

M1 can be done at any time before M2. The code tasks don't need it, because all their tests are offline. M-GH comes after T12, because it needs the `demo-repo/` files. The PAT is created in M-GH because a fine-grained PAT can only be scoped to a repo that already exists.

## Manual steps (full)

### M1: Accounts and credentials
1. **Claude token.** In a terminal, run `claude setup-token`. Complete the browser login with your Pro/Max account and copy the `sk-ant-oat...` token. Put it in `.env` as `CLAUDE_CODE_OAUTH_TOKEN=`.
2. **ngrok.**
   1. Sign up at https://dashboard.ngrok.com/signup.
   2. Go to *Getting Started → Your Authtoken* and copy the token into `NGROK_AUTHTOKEN=`.
   3. Go to *Universal Gateway → Domains* (or *Cloud Edge → Domains*) and click **+ New Domain** to claim the free static domain, e.g. `something-random.ngrok-free.app`.
   4. Put the bare host name (no `https://`) in `NGROK_DOMAIN=`.
3. The GitHub repo and PAT are handled in **M-GH** below.
4. **Webhook secret.** In PowerShell, run `[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))`. The Git Bash alternative is `openssl rand -hex 32`. Put the result in `GITHUB_WEBHOOK_SECRET=`. You'll need this same value in M2.
5. Leave `VERIFY_TIMEOUT_S=600` and `PUBLISH_MIN_SEVERITY=medium` as they are. Never commit `.env`; it's already gitignored.

### M-GH: Publish the sample repo to GitHub (after T12)
Why it's built this way: GitHub only sends `pull_request` webhooks for PRs, and a PR needs a base branch (`main`) and a head branch. So `main` holds the clean app, and each scenario is a branch that changes it. The diff between them is what PR-Gate reviews.

1. **Create the empty repo.**
   1. On github.com, click **+ → New repository**.
   2. Owner: you. Name: `pr-gate-demo`. Choose Private (recommended) or Public.
   3. Leave *Add a README*, *.gitignore* and *license* **all unticked**. It must be empty.
   4. Click **Create repository** and copy the HTTPS URL, `https://github.com/<you>/pr-gate-demo.git`.
2. **Push the clean base to `main`.** Run this in PowerShell:
   ```powershell
   Copy-Item -Recurse C:\ISP\pr-gate\demo-repo\base C:\ISP\pr-gate-demo
   cd C:\ISP\pr-gate-demo
   git init -b main
   git add .
   git commit -m "Initial clean demo app"
   git remote add origin https://github.com/<you>/pr-gate-demo.git
   git push -u origin main
   ```
   Git Credential Manager opens a browser login the first time you push.
3. **Create the vulnerable branch.** Push it, but don't open the PR yet:
   ```powershell
   git checkout -b vuln-pr
   Copy-Item -Recurse -Force C:\ISP\pr-gate\demo-repo\prs\vuln\* .
   git add .
   git commit -m "Add user search and payment config"
   git push -u origin vuln-pr
   ```
   If GitHub **push protection** blocks the push because of the fake key, open the link it prints, choose *"It's used in tests"*, allow it, and push again. The key is fake, and that's exactly what the demo needs.
4. **Create the hostile branch** from `main`:
   ```powershell
   git checkout main
   git checkout -b hostile-pr
   Copy-Item -Recurse -Force C:\ISP\pr-gate\demo-repo\prs\hostile\* .
   git add .
   git commit -m "Add helper and project settings"
   git push -u origin hostile-pr
   git checkout main
   ```
   Check that `.claude\settings.json` was copied. `Copy-Item *` includes dot-folders, but confirm with `git show --stat HEAD~0` on the branch before you push.
5. **Create the fine-grained PAT** (the repo has to exist first):
   1. Go to GitHub → avatar → *Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token*.
   2. Name it `pr-gate`. Set Expiration to 30–90 days and Resource owner to yourself.
   3. Under *Repository access*, choose **Only select repositories** and pick `pr-gate-demo`.
   4. Under *Permissions → Repository permissions*, set **Contents: Read-only** and **Pull requests: Read and write**. *Metadata: Read-only* is added automatically.
   5. Click **Generate token** and copy it right away, because it's shown only once.
6. **Update `C:\ISP\pr-gate\.env`:**
   ```
   GITHUB_TOKEN=github_pat_...
   WATCHED_REPOS=<you>/pr-gate-demo
   ```
   Separate several repos with commas. The value is `owner/name`, exactly as it appears in the repo URL.
7. **Check:** `https://github.com/<you>/pr-gate-demo/branches` should list `main`, `vuln-pr` and `hostile-pr`. **Don't open PRs yet.** The webhook has to be registered first (M2), or the `opened` event is lost.

### M2: Start the stack and register the webhook
1. Start Docker Desktop. In `C:\ISP\pr-gate`, run `docker compose up -d --build`, then `docker compose ps`. Both services should be `running`.
2. Check the local API with `curl http://localhost:8000/api/health`, which should return `{"status":"ok"}`. Then check the ngrok block with `curl https://<NGROK_DOMAIN>/api/runs`, which should return `404 Not found`.
3. Go to GitHub → `pr-gate-demo` → *Settings → Webhooks → Add webhook* and fill it in:
   - **Payload URL:** `https://<NGROK_DOMAIN>/webhook/github`
   - **Content type:** `application/json`
   - **Secret:** the same value as `GITHUB_WEBHOOK_SECRET`
   - **SSL verification:** enabled
   - **Events:** *Let me select individual events*. Untick *Pushes*, tick only **Pull requests**.
   - **Active:** ticked
4. Click Add webhook, then open it → *Recent Deliveries*. The `ping` should show **200**. If it doesn't, run `docker compose logs backend ngrok`.
5. Start the dashboard: `cd dashboard`, `npm install`, then `npm run dev`. Open http://localhost:5173.

### M3: Demo PRs and end-to-end checks
1. On GitHub, go to `pr-gate-demo` → *Pull requests → New pull request*, set base `main` and compare `vuln-pr`, then click **Create pull request**. Check that the webhook delivery shows 202, the dashboard run goes queued → running → completed, and one review appears on the PR.
2. Open a second PR the same way with compare `hostile-pr`. The verdicts should be valid, the run detail should show that the `.claude/` and `CLAUDE.md` files were stripped before verification, and the injected instruction should not change any verdict.
3. Run the forged-POST script. It should get 401.
4. Push twice quickly to `vuln-pr` to test supersede.
5. Temporarily set `VERIFY_TIMEOUT_S=5`, run `docker compose up -d`, and push again. The run should fail, nothing should be posted, and `docker compose exec backend ls /workspaces` should be empty. Then restore the setting.

## Verification
- Every code task: `docker compose run --rm backend pytest -q` is green before its commit.
- T11: `npm run build` in `dashboard/` passes, and the preview browser shows the pages rendering against the running backend.
- The end-to-end checklist from IMPLEMENTATION_PLAN.md §Verification, items 1–8, is carried out in M2/M3.
