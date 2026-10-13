"""Read-only dashboard API (localhost only; see the Host guard in main.py)."""

import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response

router = APIRouter(prefix="/api")

SEVERITIES = ("high", "medium", "low")
VERDICTS = ("confirmed", "false_positive", "unclear")


def pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


@router.get("/runs")
def list_runs(request: Request):
    db = request.app.state.db
    counts = db.finding_counts_by_run()
    out = []
    for run in db.list_runs():
        c = counts.get(run["id"], {})
        out.append({
            **run,
            "severity_counts": {s: c.get(("severity", s), 0) for s in SEVERITIES},
            "verdict_counts": {v: c.get(("verdict", v), 0) for v in VERDICTS},
        })
    return out


@router.get("/runs/{run_id}")
def get_run(run_id: int, request: Request):
    db = request.app.state.db
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    findings = [{**f, "in_diff": bool(f["in_diff"]), "published": bool(f["published"])}
                for f in db.findings_for_run(run_id)]
    return {"run": run, "findings": findings, "tool_reports": db.tool_reports_for_run(run_id),
            "has_artifacts": bool(artifact_files(request, run_id))}


def artifact_files(request: Request, run_id: int) -> list[Path]:
    d = Path(request.app.state.settings.artifacts_dir) / str(run_id)
    return sorted(p for p in d.iterdir() if p.is_file()) if d.is_dir() else []


@router.get("/runs/{run_id}/artifacts.zip")
def download_artifacts(run_id: int, request: Request):
    if request.app.state.db.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    files = artifact_files(request, run_id)
    if not files:
        raise HTTPException(status_code=404, detail="no tool outputs saved for this run")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, arcname=p.name)
    return Response(buf.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="pr-gate-run-{run_id}-tool-outputs.zip"'})


@router.get("/stats/tools")
def tool_stats(request: Request):
    out = []
    for row in request.app.state.db.tool_verdict_stats():
        classified = row["confirmed"] + row["false_positive"] + row["unclear"]
        out.append({
            **row,
            "confirmed_pct": pct(row["confirmed"], classified),
            "false_positive_pct": pct(row["false_positive"], classified),
        })
    return out
