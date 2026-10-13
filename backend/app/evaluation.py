"""B8: human labels vs. agent verdicts, and a CSV export for the report."""

import csv
import io

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.db import HUMAN_LABELS

router = APIRouter(prefix="/api")

DASHBOARD_ORIGIN = "http://localhost:5173"
QUEUE_SIZE = 20
CSV_COLUMNS = ["run_id", "repo", "pr", "head_sha", "finding_id", "tool", "rule_id", "severity", "path", "line",
               "in_diff", "verdict", "published", "human_label", "labelled_at", "message", "justification"]


def pct(part: int, whole: int) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


def metrics(rows: list[dict]) -> dict:
    pairs = [(r["verdict"], r["human_label"]) for r in rows if r["verdict"] and r["human_label"]]
    agent_confirmed = [h for v, h in pairs if v == "confirmed"]
    agent_fp = [h for v, h in pairs if v == "false_positive"]
    return {
        "labelled": len(pairs),
        "agreement": pct(sum(v == h for v, h in pairs), len(pairs)),
        "agent_precision": pct(agent_confirmed.count("confirmed"), len(agent_confirmed)),
        "fp_filter_accuracy": pct(agent_fp.count("false_positive"), len(agent_fp)),
    }


@router.post("/findings/{finding_row_id}/label")
async def set_label(finding_row_id: int, request: Request):
    # First write endpoint: only the dashboard may call it. Requiring JSON forces a CORS preflight,
    # and the Origin check stops other sites in the user's browser from labelling (CSRF).
    if request.headers.get("origin") != DASHBOARD_ORIGIN:
        return JSONResponse({"error": "forbidden origin"}, status_code=403)
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        return JSONResponse({"error": "content type must be application/json"}, status_code=415)
    try:
        body = await request.json()
        value = body["label"]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=400, detail="body must be {\"label\": ...}")
    if value is not None and value not in HUMAN_LABELS:
        raise HTTPException(status_code=400, detail="label must be confirmed, false_positive, unclear or null")
    db = request.app.state.db
    if not db.set_human_label(finding_row_id, value):
        raise HTTPException(status_code=404, detail="finding not found")
    return db.get_finding(finding_row_id)


@router.get("/evaluation")
def evaluation(request: Request):
    db = request.app.state.db
    rows = db.findings_with_runs()
    tools = sorted({r["tool"] for r in rows if r["verdict"] and r["human_label"]})
    return {
        "overall": metrics(rows),
        "by_tool": [{"tool": t, **metrics([r for r in rows if r["tool"] == t])} for t in tools],
        "queue": db.label_queue(QUEUE_SIZE),
    }


def csv_safe(value) -> str:
    """Stop spreadsheet formula injection from tool or agent text."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.get("/export/findings.csv")
def export_csv(request: Request):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in request.app.state.db.findings_with_runs():
        writer.writerow({k: csv_safe(row.get(k)) for k in CSV_COLUMNS})
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="pr-gate-findings.csv"'})
