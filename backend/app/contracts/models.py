import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

SCHEMA_PATH = Path(__file__).with_name("classification.schema.json")
CLASSIFICATION_SCHEMA = json.loads(SCHEMA_PATH.read_text())

Severity = Literal["high", "medium", "low"]
Verdict = Literal["confirmed", "false_positive", "unclear"]
SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}


class RunRequest(BaseModel):
    repo: str
    pr: int
    head_ref: str
    base_sha: str
    head_sha: str


class Finding(BaseModel):
    id: str
    tool: str
    rule_id: str
    severity: Severity
    path: str
    line: int  # 1-based; 0 when the tool gives no line
    message: str


class Classification(BaseModel):
    finding_id: str
    verdict: Verdict
    justification: str
