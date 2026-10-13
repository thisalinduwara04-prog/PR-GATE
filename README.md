# PR-Gate: AI-augmented code review gate

PR-Gate reviews every pull request on a GitHub repository for security problems:

1. **Scan.** It runs three static-analysis tools on the PR: **Semgrep** (code patterns), **Gitleaks** (secrets) and **OSV-Scanner** (vulnerable dependencies).
2. **Verify.** A sandboxed **Claude Code** agent reads the actual code behind every finding and labels it *confirmed*, *false positive* or *unclear*, with a written justification.
3. **Review.** It posts **one advisory review** on the PR, with confirmed findings inline on the changed lines, other confirmed findings and "unresolved" ones in the summary, and false positives hidden. It also sets a `pr-gate` commit status.
4. **Dashboard.** A local dashboard shows every run and every finding, including discarded ones. You can download exactly what the agent saw, and **measure the agent's accuracy** by labelling findings yourself.

Static analysers are noisy; reviewers learn to ignore them. PR-Gate keeps the tools' recall, but uses an LLM agent to filter their false positives *before* a human sees them. It is built so that **a malicious pull request can't take over the agent, hide its own findings or steal secrets.**

> **Setting it up?** Follow **[SETUP.md](SETUP.md)**. It is written so you can tell your AI coding agent: *"Read SETUP.md and set up this project."*

---

## How a pull request flows through PR-Gate

1. **Trigger.** A PR is opened or updated. GitHub sends an HMAC-signed webhook, which ngrok forwards to the backend.
2. **Accept.** The backend:
   - verifies the signature
   - ignores duplicate deliveries
   - filters the event and repository
   - stores a `queued` run
   - records the PR's latest commit
   - cancels any older, still-running run of the same PR
3. **Check out.** A worker fetches the PR's head and base commits into `/workspaces/<run>/src`. It then:
   - removes everything a PR could use to manipulate the agent (`CLAUDE.md`, `.claude/`, `.mcp.json`) or blind the scanners (`.semgrepignore`, `.gitleaks.toml`, `osv-scanner.toml`, …)
   - never materialises symlinks
   - makes the tree read-only
4. **Diff.** It runs `git merge-base` and diffs that base against the PR head, to know which lines the PR changed.
5. **Scan.** The **scanner container** runs the three tools and returns raw SARIF. The backend normalises the results into findings (tool, rule, severity, path, line, message) and de-duplicates them.
6. **Verify.** The backend builds a prompt from the findings and the diff. The repository content is fenced as untrusted data. The **agent container** runs `claude -p` in the checkout with read-only tools. The answer must match a JSON schema and classify every finding exactly once, or the run fails and **nothing is published**.
7. **Supersede check.** If a newer commit arrived meanwhile, nothing is published.
8. **Publish.** One `COMMENT` review is posted (never approve or request-changes), and the `pr-gate` status goes `pending → success` or `error`.
9. **Record.** Everything goes to SQLite for the dashboard: the run, the findings, verdicts, justifications, tool statuses, the raw reports and the exact prompt and answer. The workspace is deleted.

---

## Architecture

```
                     GitHub (repo, PRs, webhooks, reviews, commit statuses)
                        │ signed webhook                       ▲ review + status (REST API)
                        ▼                                      │
 ┌──────── network "edge" ──────────┐                          │
 │ ngrok  (forwards ONLY            │                          │
 │        /webhook/github)  ────────┼──► backend :8000 ────────┘
 └──────────────────────────────────┘    FastAPI · webhook · worker pool · git checkout/diff
                                         normalise · prompt · validate · publish · SQLite
                                         secrets: GitHub token, webhook secret
                                              │ HTTP (internal only)        ▲ GET/POST /api (localhost only)
 ┌──────── network "internal" ─────────────────┼──────────────────────┐     │
 │  scanner :9000   semgrep · gitleaks · osv   │  no secrets          │   React dashboard :5173
 │  agent   :9100   claude CLI (Read/Grep/Glob)│  Claude token only   │   (Vite, on the host)
 └─────────────────────────────────────────────┴──────────────────────┘
          volumes:  workspaces (backend rw · scanner/agent READ-ONLY)  ·  data (backend only)
```

| Container | Image target | Runs as | Can see |
|---|---|---|---|
| `backend` | `backend` | uid 1000 | GitHub token, webhook secret, database, workspaces (read-write) |
| `scanner` | `scanner` | uid 1100 | workspaces **read-only**, nothing else; no secrets |
| `agent` | `agent` | uid 1200 | workspaces **read-only** and the Claude token; no GitHub token, no database |
| `ngrok` | pinned upstream image | ngrok user | its authtoken; only the `edge` network |

Every container has a read-only root filesystem (tmpfs for scratch), drops all Linux capabilities, sets `no-new-privileges`, and has memory, CPU and process limits. ngrok can't even resolve the scanner or agent.

---

## Repository layout

```
backend/
  Dockerfile                multi-target: backend, scanner, agent, test
  app/
    main.py                 FastAPI app, lifespan (worker pool, startup catch-up), localhost guard, CORS
    webhook.py              POST /webhook/github: size cap, HMAC, duplicate guard, filtering, cancel older runs
    api.py                  dashboard API: runs, run detail, tool stats, artifacts.zip
    evaluation.py           human labels, accuracy metrics, CSV export
    catchup.py              on startup, queue open PRs whose head was never reviewed
    worker.py               queue + worker pool (MAX_CONCURRENT_RUNS)
    db.py                   SQLite (WAL), parameterised queries, in-place migrations
    config.py               settings from environment variables
    contracts/              Pydantic models + the agent's JSON output schema
    normalizers/            one SARIF parser + per-tool severity rules (registry)
    pipeline/
      run.py                one run: checkout → diff → scan → verify → publish, supersede, cleanup
      steps.py              binds the steps to settings, DB, HTTP; local or remote (B5) mode
      workspace.py          token-safe fetch, sanitiser, read-only tree
      diff.py               merge-base diff → changed lines per file
      scan.py               run tools (scanner side) and normalise (backend side)
      verify.py             prompt, claude invocation, retries, strict validation
      publish.py            review payload, commit status
      remote.py             backend clients for the scanner and agent services
      proc.py               subprocess helper: no shell, process groups, timeouts, cancellation
    services/
      scanner.py            internal scanner service (POST /scan, /cancel)
      agent.py              internal agent service (POST /verify, /cancel)
    prompts/verify.md       the agent's instructions
  config/                   local Semgrep rules, Gitleaks/OSV config overrides
  tests/                    pytest suite (about 160 tests) + SARIF fixtures + fake claude
dashboard/                  Vite + React + TypeScript dashboard
demo-repo/                  deliberately vulnerable sample project + PR overlays + publish scripts
ngrok/                      traffic policy (only /webhook/github) and agent config (inspection UI off)
scripts/test.sh             builds the test image and runs pytest in it
docs/superpowers/specs/     design specs for each phase
docker-compose.yml          the four-container, hardened stack
```

---

## Tech stack

| Area | Choice |
|---|---|
| Backend | Python 3.11, FastAPI, Uvicorn, Pydantic, httpx, SQLite (WAL) |
| Analysis tools | Semgrep 1.177 (local, offline rules), Gitleaks 8.30, OSV-Scanner 2.6 |
| AI agent | Claude Code CLI 2.1.278 in `--restricted` mode with Read/Grep/Glob only, structured JSON output |
| Dashboard | React 19, Vite, TypeScript, React Router, TanStack Query |
| Infrastructure | Docker Compose (four containers), ngrok static domain with a traffic policy |
| Tests | pytest (+ real scanners, a local git remote, a fake `claude`, mocked GitHub API), dashboard type-check/build |

---

## Dashboard

| Page | What it shows |
|---|---|
| **Runs** | Every run with status, PR, head commit, severity counts and verdict counts; refreshes while runs are active |
| **Run detail** | Run metadata and failure reason; per-tool status (including what the sanitiser removed); every finding with filters, the tool message, the agent's justification, whether it was published, and a human-label control; **Download tool outputs (.zip)** containing the raw SARIF, `agent_input.json`, `agent_prompt.md` and `agent_output.json` |
| **Tool stats** | Per tool: how often the agent confirmed or rejected its findings |
| **Evaluation** | Your labels vs. the agent's verdicts: **agreement**, **agent precision** and **FP-filter accuracy**, overall and per tool; a random labelling queue; **CSV export** of all findings |

---

## Security design

**Inbound**
- The only public path is `POST /webhook/github`, through ngrok's traffic policy. Every other public URL returns 404.
- Webhooks: body size cap, HMAC-SHA256 check with constant-time compare (an empty secret rejects everything), duplicate-delivery guard, event/action/repository allow-list.
- Dashboard API:
  - It only answers when the `Host` header is `localhost` or `127.0.0.1`.
  - CORS allows only `http://localhost:5173`.
  - The single write endpoint (setting a label) also requires the dashboard `Origin` and a JSON body, which blocks CSRF from other sites in your browser.

**Untrusted PR content**
- The checkout is stripped of agent configuration and scanner-suppression files, has no symlinks, and is read-only. The git directory lives outside the scanned tree.
- Scanners ignore inline suppressions (`nosemgrep`, `gitleaks:allow`), get forced configs, run without a shell in their own process group, and get a minimal environment with no tokens. One failing tool doesn't stop the others.
- The agent:
  - Runs `claude -p --restricted --tools Read,Grep,Glob --permission-mode dontAsk --strict-mcp-config`, with explicit denies for `/proc`, `/data`, `/home`, `/app` and `/etc`.
  - Is deliberately not given `--allowedTools`, because that would pre-approve reads outside the workspace.
  - Gets the prompt on stdin, with nonce-delimited untrusted blocks.
  - Must return output that matches the schema and covers every finding id, or nothing is published.

**Secrets and isolation**
- Each container gets only the secrets it needs (see the table above).
- The GitHub token travels only in `GIT_CONFIG_*` environment variables: never in argv, `.git/config` or error messages.
- Base images are pinned by digest, and downloaded binaries are checksum-verified.

**Output**
- One `COMMENT` review only, never approve or request-changes. False positives are never posted, and `@mentions` are defused.
- The scanner's secret message is masked. The agent's justification is published verbatim (see the limitations).
- The `pr-gate` status is advisory: success means "the review ran", not "no issues".

---

## Configuration (`.env`)

| Variable | Meaning |
|---|---|
| `GITHUB_TOKEN` | Fine-grained PAT for the watched repo(s): Contents read, Pull requests read/write, Commit statuses read/write |
| `GITHUB_WEBHOOK_SECRET` | Shared secret for webhook signatures |
| `WATCHED_REPOS` | `owner/name[,owner/name…]` |
| `CLAUDE_CODE_OAUTH_TOKEN` | From `claude setup-token` (Claude Pro/Max) |
| `NGROK_AUTHTOKEN`, `NGROK_DOMAIN` | ngrok agent token and your static domain (host name only) |
| `VERIFY_TIMEOUT_S` | Time limit for the agent per run (default 600) |
| `PUBLISH_MIN_SEVERITY` | `low`/`medium`/`high`: findings below this are dashboard-only (default medium) |
| `COMMIT_STATUS_ENABLED` | Post the `pr-gate` commit status (default true) |
| `CATCHUP_ON_STARTUP` | Review open PRs missed while offline (default true) |
| `MAX_CONCURRENT_RUNS` | Runs processed in parallel (default 2) |

Step-by-step instructions are in [SETUP.md](SETUP.md).

---

## Development

```bash
scripts/test.sh                        # backend tests, in Docker (Git Bash on Windows)
scripts/test.sh tests/test_webhook.py  # a subset
cd dashboard && npm run build          # type-check and build the dashboard
docker compose up -d --build           # run the full stack with your changes
```

- **Two modes.** With `SCANNER_URL` and `AGENT_URL` set (as in compose), the backend calls the scanner and agent containers. Without them, it runs the tools and `claude` in-process, which is what most tests use.
- **How the tests work.** They run the real scanners and real git, but a **fake `claude`** (`tests/fake_claude.py`) and a mocked GitHub API, so they're deterministic and need no accounts.
- **Docs.** The design spec for each phase is in `docs/superpowers/specs/`.

---

## Known limitations

- A personal access token is used, not a GitHub App.
- The dashboard has no authentication and is for a trusted local machine. Dashboard ↔ API traffic is plain HTTP on localhost; everything leaving the machine uses HTTPS.
- The review is advisory and never blocks merging. A blocking check would need branch protection, which is free on public repos only.
- Webhooks sent while offline are recovered at the next startup (open PRs only). GitHub's *Redeliver* is ignored by the duplicate guard; push a commit to re-run.
- OSV findings for transitive dependencies have no line number, so they appear in the review body.
- The agent's justification is published verbatim. For secrets it has been seen quoting the value despite instructions; only the scanner's own message is masked.
- Antivirus HTTPS inspection (e.g. Kaspersky) can break the agent's connection to Anthropic. See [SETUP.md → Troubleshooting](SETUP.md#troubleshooting).
- Not implemented: Checkstyle and SonarQube (Part B4).
