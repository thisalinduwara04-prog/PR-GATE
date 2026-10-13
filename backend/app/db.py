"""SQLite persistence. Every query is parameterised; column names are whitelisted."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.contracts.models import Classification, Finding, RunRequest

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    pr INTEGER NOT NULL,
    head_ref TEXT NOT NULL,
    base_sha TEXT NOT NULL,
    head_sha TEXT NOT NULL,
    merge_base TEXT,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    review_id INTEGER
);
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    finding_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    path TEXT NOT NULL,
    line INTEGER NOT NULL,
    message TEXT NOT NULL,
    in_diff INTEGER NOT NULL DEFAULT 0,
    verdict TEXT,
    justification TEXT,
    published INTEGER NOT NULL DEFAULT 0,
    UNIQUE (run_id, finding_id)
);
CREATE TABLE IF NOT EXISTS tool_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    tool TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS pr_latest (
    repo TEXT NOT NULL,
    pr INTEGER NOT NULL,
    head_sha TEXT NOT NULL,
    PRIMARY KEY (repo, pr)
);
CREATE TABLE IF NOT EXISTS deliveries (
    delivery_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL
);
"""

RUN_UPDATABLE = {"status", "error", "merge_base", "started_at", "finished_at", "review_id"}
# Columns added after the MVP: (table, column, definition). Applied in place by init().
MIGRATIONS = [
    ("findings", "human_label", "TEXT"),
    ("findings", "labelled_at", "TEXT"),
]
HUMAN_LABELS = {"confirmed", "false_positive", "unclear"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str):
        self.path = path

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.execute("PRAGMA journal_mode = WAL")
            c.executescript(SCHEMA)
            for table, column, definition in MIGRATIONS:
                existing = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
                if column not in existing:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    # --- runs -------------------------------------------------------------
    def create_run(self, req: RunRequest) -> int:
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO runs (repo, pr, head_ref, base_sha, head_sha, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'queued', ?)",
                (req.repo, req.pr, req.head_ref, req.base_sha, req.head_sha, now_iso()),
            )
            return cur.lastrowid

    def get_run(self, run_id: int) -> dict | None:
        with self.connect() as c:
            row = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            return dict(row) if row else None

    def update_run(self, run_id: int, **fields) -> None:
        bad = set(fields) - RUN_UPDATABLE
        if bad:
            raise ValueError(f"not updatable: {sorted(bad)}")
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.connect() as c:
            c.execute(f"UPDATE runs SET {cols} WHERE id = ?", (*fields.values(), run_id))

    def run_ids_with_status(self, status: str) -> list[int]:
        with self.connect() as c:
            rows = c.execute("SELECT id FROM runs WHERE status = ? ORDER BY id", (status,)).fetchall()
            return [r["id"] for r in rows]

    def list_runs(self) -> list[dict]:
        with self.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM runs ORDER BY id DESC").fetchall()]

    # --- pr_latest --------------------------------------------------------
    def upsert_pr_latest(self, repo: str, pr: int, head_sha: str) -> None:
        with self.connect() as c:
            c.execute(
                "INSERT INTO pr_latest (repo, pr, head_sha) VALUES (?, ?, ?) "
                "ON CONFLICT (repo, pr) DO UPDATE SET head_sha = excluded.head_sha",
                (repo, pr, head_sha),
            )

    def get_pr_latest(self, repo: str, pr: int) -> str | None:
        with self.connect() as c:
            row = c.execute("SELECT head_sha FROM pr_latest WHERE repo = ? AND pr = ?", (repo, pr)).fetchone()
            return row["head_sha"] if row else None

    # --- findings ---------------------------------------------------------
    def insert_findings(self, run_id: int, findings: list[Finding], in_diff: set[str]) -> None:
        with self.connect() as c:
            c.executemany(
                "INSERT INTO findings (run_id, finding_id, tool, rule_id, severity, path, line, message, in_diff) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(run_id, f.id, f.tool, f.rule_id, f.severity, f.path, f.line, f.message, int(f.id in in_diff))
                 for f in findings],
            )

    def set_verdicts(self, run_id: int, classifications: list[Classification]) -> None:
        with self.connect() as c:
            c.executemany(
                "UPDATE findings SET verdict = ?, justification = ? WHERE run_id = ? AND finding_id = ?",
                [(k.verdict, k.justification, run_id, k.finding_id) for k in classifications],
            )

    def mark_published(self, run_id: int, finding_ids: list[str]) -> None:
        with self.connect() as c:
            c.executemany(
                "UPDATE findings SET published = 1 WHERE run_id = ? AND finding_id = ?",
                [(run_id, fid) for fid in finding_ids],
            )

    def findings_for_run(self, run_id: int) -> list[dict]:
        with self.connect() as c:
            rows = c.execute("SELECT * FROM findings WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
            return [dict(r) for r in rows]

    def finding_counts_by_run(self) -> dict[int, dict[tuple[str, str], int]]:
        """{run_id: {("severity", "high"): n, ("verdict", "confirmed"): n, ...}}"""
        out: dict[int, dict[tuple[str, str], int]] = {}
        with self.connect() as c:
            for col in ("severity", "verdict"):
                rows = c.execute(
                    f"SELECT run_id, {col} AS k, COUNT(*) AS n FROM findings "
                    f"WHERE {col} IS NOT NULL GROUP BY run_id, {col}"
                ).fetchall()
                for r in rows:
                    out.setdefault(r["run_id"], {})[(col, r["k"])] = r["n"]
        return out

    def tool_verdict_stats(self) -> list[dict]:
        with self.connect() as c:
            rows = c.execute(
                "SELECT tool, COUNT(*) AS total, "
                "SUM(verdict = 'confirmed') AS confirmed, "
                "SUM(verdict = 'false_positive') AS false_positive, "
                "SUM(verdict = 'unclear') AS unclear "
                "FROM findings GROUP BY tool ORDER BY tool"
            ).fetchall()
            return [{k: (r[k] or 0) if k != "tool" else r[k] for k in r.keys()} for r in rows]

    # --- tool reports -----------------------------------------------------
    def add_tool_report(self, run_id: int, tool: str, status: str, error: str | None) -> None:
        with self.connect() as c:
            c.execute(
                "INSERT INTO tool_reports (run_id, tool, status, error) VALUES (?, ?, ?, ?)",
                (run_id, tool, status, error),
            )

    def tool_reports_for_run(self, run_id: int) -> list[dict]:
        with self.connect() as c:
            rows = c.execute("SELECT * FROM tool_reports WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
            return [dict(r) for r in rows]

    # --- deliveries (B3) --------------------------------------------------
    def record_delivery(self, delivery_id: str) -> bool:
        """True if this delivery id is new; False if it was already seen."""
        with self.connect() as c:
            cur = c.execute("INSERT OR IGNORE INTO deliveries (delivery_id, received_at) VALUES (?, ?)",
                            (delivery_id, now_iso()))
            return cur.rowcount == 1

    def run_exists(self, repo: str, pr: int, head_sha: str) -> bool:
        with self.connect() as c:
            row = c.execute("SELECT 1 FROM runs WHERE repo = ? AND pr = ? AND head_sha = ? LIMIT 1",
                            (repo, pr, head_sha)).fetchone()
            return row is not None

    # --- human labels (B8) -----------------------------------------------
    def set_human_label(self, finding_row_id: int, label: str | None) -> bool:
        if label is not None and label not in HUMAN_LABELS:
            raise ValueError(f"invalid label: {label!r}")
        with self.connect() as c:
            cur = c.execute("UPDATE findings SET human_label = ?, labelled_at = ? WHERE id = ?",
                            (label, now_iso() if label else None, finding_row_id))
            return cur.rowcount == 1

    def findings_with_runs(self) -> list[dict]:
        with self.connect() as c:
            rows = c.execute(
                "SELECT f.*, r.repo, r.pr, r.head_sha FROM findings f JOIN runs r ON r.id = f.run_id "
                "ORDER BY f.run_id, f.id"
            ).fetchall()
            return [dict(r) for r in rows]

    def label_queue(self, limit: int) -> list[dict]:
        """Random sample of findings the agent classified but a human has not labelled yet."""
        with self.connect() as c:
            rows = c.execute(
                "SELECT f.*, r.repo, r.pr, r.head_sha FROM findings f JOIN runs r ON r.id = f.run_id "
                "WHERE f.verdict IS NOT NULL AND f.human_label IS NULL ORDER BY RANDOM() LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_finding(self, finding_row_id: int) -> dict | None:
        with self.connect() as c:
            row = c.execute("SELECT * FROM findings WHERE id = ?", (finding_row_id,)).fetchone()
            return dict(row) if row else None

    def running_runs_for_pr(self, repo: str, pr: int, except_head: str) -> list[int]:
        """B6: runs of this PR still queued or running for an older head commit."""
        with self.connect() as c:
            rows = c.execute(
                "SELECT id FROM runs WHERE repo = ? AND pr = ? AND head_sha != ? AND status IN ('queued', 'running')",
                (repo, pr, except_head),
            ).fetchall()
            return [r["id"] for r in rows]
