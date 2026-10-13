# PR-Gate Phase B: B1, B2, B3, B8 and tool-output download

## Context
The MVP (Part A) is merged. This phase adds four Part B increments from `IMPLEMENTATION_PLAN.md` (B1 startup catch-up, B2 commit status, B3 duplicate-delivery guard, B8 evaluation). It also adds a user-requested feature: downloading the tool outputs that are passed to the AI agent.

## Tool-output download
- Each run saves the following to `/data/artifacts/<run_id>/` (setting `ARTIFACTS_DIR`), even if the run later fails:
  - raw SARIF: `semgrep.sarif`, `gitleaks.sarif`, `osv.sarif`
  - `agent_input.json`: `{findings, diff}`
  - `agent_prompt.md`: the exact prompt sent to Claude
  - `agent_output.json`: Claude's raw CLI output from the last attempt
- `GET /api/runs/{id}/artifacts.zip` returns a zip with `Content-Disposition: attachment`, or 404 when the run has no artifacts.
- `GET /api/runs/{id}` adds `has_artifacts`.
- The dashboard's run detail page has a "Download tool outputs (.zip)" link.

## B1: startup catch-up
- On startup, a background thread lists the open PRs of each watched repo (`GET /repos/{repo}/pulls?state=open`, paginated).
- For each PR whose `(repo, pr, head_sha)` has no run, it upserts `pr_latest`, creates a queued run and enqueues it.
- Errors are logged and never block startup. Setting: `CATCHUP_ON_STARTUP` (default true).

## B2: commit status
- Context `pr-gate`, posted to `POST /repos/{repo}/statuses/{head_sha}`:
  - `pending` when the run starts
  - `success` when it completes, with a description like "3 confirmed, 1 unresolved (advisory)"
  - `error` when it fails, with the error truncated to GitHub's 140-character limit
- A superseded run gets no final status; the newer run owns the new SHA.
- `target_url` is `http://localhost:5173/runs/<id>`.
- A failure to set the status is logged and never fails the run. Setting: `COMMIT_STATUS_ENABLED` (default true).
- **Manual step:** the PAT needs *Commit statuses: Read and write*.
- Advisory only: success means "the review ran", not "no findings".

## B3: duplicate-delivery guard
- New table `deliveries(delivery_id PRIMARY KEY, received_at)`.
- After the signature check, a `pull_request` delivery whose `X-GitHub-Delivery` id was already seen gets `200 {"ignored": "duplicate delivery"}`. The check is an atomic `INSERT OR IGNORE`.
- GitHub's "Redeliver" button reuses the delivery id, so it is ignored. To re-run a PR, push a commit or restart the backend (B1).

## B8: evaluation
- `findings` gains `human_label` (confirmed / false_positive / unclear / NULL) and `labelled_at`. The DB is migrated in place with `ALTER TABLE ... ADD COLUMN` when the columns are missing.
- `POST /api/findings/{id}/label` takes `{"label": "confirmed" | "false_positive" | "unclear" | null}`. It is the first write endpoint, so it has CSRF defences:
  - the localhost Host guard
  - `Content-Type: application/json` is required (which forces a CORS preflight)
  - `Origin` must equal `http://localhost:5173`
  - CORS allows `POST` from the dashboard origin only
- `GET /api/evaluation`:
  - Returns overall and per-tool metrics, each computed over findings that have both an agent verdict and a human label:
    - `labelled`
    - `agreement`: the share where the agent verdict equals the human label
    - `agent_precision`: of the agent's `confirmed` verdicts, the share the human also labelled `confirmed`
    - `fp_filter_accuracy`: of the agent's `false_positive` verdicts, the share the human also labelled `false_positive`
  - Also returns `queue`: up to 20 random findings that have an agent verdict but no human label.
- `GET /api/export/findings.csv`: every finding with run, repo, PR, head SHA, tool, rule, severity, location, in_diff, verdict, justification, published and human_label.
- Dashboard:
  - The run detail page gets a human-label select in each expanded finding.
  - A new **Evaluation** page shows the metrics table, the labelling queue with inline selects, and a CSV download link.

## Testing
- Each task is test-first, with `scripts/test.sh` green before each commit.
- End to end: a new run on `NadithL/pr-gate-demo` PR #3 must show:
  - a `pr-gate` status on the head commit
  - the artifacts zip downloadable from the dashboard
  - labels saved and the metrics updated

## Addendum: B5, B6 and B7 (same branch)
- **B5:** scanning and the Claude CLI moved into `scanner` and `agent` services (`app/services/`), each in its own container. They are reached by the backend over an internal network and mount the workspaces read-only. The backend keeps checkout, normalisation, prompt building, validation, retries and publishing. With `SCANNER_URL`/`AGENT_URL` unset, it runs everything in-process; most tests use that mode.
- **B6:** `MAX_CONCURRENT_RUNS` workers share the queue (default 2). Processes are registered per run id, and a newer push cancels older running runs of the same PR (locally and in both services). The cancelled run ends as `superseded`.
- **B7:** one multi-target Dockerfile, a non-root user per image (1000/1100/1200), and Semgrep in its own venv. Compose adds:
  - read-only root filesystems plus tmpfs
  - all capabilities dropped and `no-new-privileges`
  - pids, memory and CPU limits
  - digest-pinned base images
  - per-service secrets
  - an `edge` and an `internal` network
  - the ngrok inspection UI disabled
- **Docs:** `README.md` describes what the project is and how it's built. `SETUP.md` is a step-by-step setup guide written for teammates' AI coding agents.
