import json
import shutil
import sys
from pathlib import Path

import pytest

from app.normalizers.registry import NORMALIZERS, normalize_report
from app.normalizers.sarif import dedupe_and_number
from app.pipeline import scan
from app.pipeline.scan import ToolSpec, run_scanners

SARIF = Path(__file__).parent / "fixtures" / "sarif"
SAMPLE = Path(__file__).parent / "fixtures" / "sample"


def load(name):
    return json.loads((SARIF / name).read_text())


def test_registry_has_three_tools():
    assert set(NORMALIZERS) == {"semgrep", "gitleaks", "osv-scanner"}


def test_semgrep_normalisation():
    fs = normalize_report("semgrep", load("semgrep.sarif"), SAMPLE)
    by_rule = {}
    for f in fs:
        by_rule.setdefault(f.rule_id, []).append(f)
    assert len(fs) == 8
    assert by_rule["python-sqli-string-built-query"][0].severity == "high"
    assert by_rule["python-sqli-string-built-query"][0].line == 17
    assert by_rule["python-sqli-string-built-query"][0].path == "app.py"
    assert by_rule["python-eval-exec-dynamic"][0].severity == "high"
    assert {f.line for f in by_rule["python-subprocess-shell-true"]} == {22, 23}
    assert by_rule["python-subprocess-shell-true"][0].severity == "medium"
    assert by_rule["python-weak-hash-md5"][0].severity == "low"
    assert all(f.tool == "semgrep" for f in fs)


def test_gitleaks_always_high_and_redacted():
    fs = normalize_report("gitleaks", load("gitleaks.sarif"), SAMPLE)
    assert len(fs) == 1
    f = fs[0]
    assert (f.severity, f.path, f.line, f.rule_id) == ("high", "app.py", 11, "generic-api-key")
    assert "x9Fq2LmZ" not in f.message


def test_osv_relative_path_line_and_cvss_severity():
    fs = normalize_report("osv-scanner", load("osv.sarif"), Path("/src"), SAMPLE)
    assert fs and all(f.path == "requirements.txt" for f in fs)
    jinja = [f for f in fs if f.rule_id == "CVE-2019-10906"]
    assert jinja and jinja[0].severity == "high"  # security-severity 8.6
    assert jinja[0].line == 3  # Jinja2==2.10 is line 3 of the sample requirements.txt
    assert {f.severity for f in fs} <= {"high", "medium", "low"}


def test_osv_line_lookup_uses_workspace_file(tmp_path):
    (tmp_path / "requirements.txt").write_text("# deps\nflask==0.12.2\nPyYAML==5.3\nJinja2==2.10\n")
    fs = normalize_report("osv-scanner", load("osv.sarif"), Path("/src"), tmp_path)
    jinja = [f for f in fs if f.rule_id == "CVE-2019-10906"]
    assert jinja[0].line == 4


def test_malformed_sarif_raises():
    with pytest.raises(ValueError):
        normalize_report("semgrep", {"runs": "nope"}, SAMPLE)


def test_dedupe_and_number():
    fs = normalize_report("semgrep", load("semgrep.sarif"), SAMPLE)
    out = dedupe_and_number(fs + fs)
    assert len(out) == 8
    assert [f.id for f in out] == [f"F{i}" for i in range(1, 9)]


# ---- scan orchestration with fake tools -------------------------------------------------

def fake_tool(tmp_path, name, body):
    script = tmp_path / f"{name}.py"
    script.write_text(body)
    return [sys.executable, str(script)]


def test_failing_and_timed_out_tools_do_not_stop_others(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    shutil.copytree(SAMPLE, ws)
    reports = tmp_path / "reports"
    reports.mkdir()
    good = fake_tool(tmp_path, "good", f"import shutil,sys; shutil.copy({str(SARIF / 'semgrep.sarif')!r}, sys.argv[1])")
    bad = fake_tool(tmp_path, "bad", "import sys; sys.stderr.write('boom'); sys.exit(3)")
    slow = fake_tool(tmp_path, "slow", "import time; time.sleep(30)")
    garbage = fake_tool(tmp_path, "garbage", "import sys; open(sys.argv[1],'w').write('not json')")
    specs = [
        ToolSpec("gitleaks", lambda ws, out: bad + [str(out)], "a.sarif", {0}),
        ToolSpec("osv-scanner", lambda ws, out: slow + [str(out)], "b.sarif", {0}),
        ToolSpec("semgrep", lambda ws, out: garbage + [str(out)], "c.sarif", {0}),
        ToolSpec("semgrep", lambda ws, out: good + [str(out)], "d.sarif", {0}),
    ]
    monkeypatch.setattr(scan, "TOOLS", specs)
    records = []
    findings = run_scanners(ws, reports, lambda *r: records.append(r), timeout_s=2)
    assert [r[1] for r in records] == ["failed", "timeout", "parse_error", "ok"]
    assert "boom" in records[0][2]
    assert len(findings) == 8 and findings[0].id == "F1"


def test_child_env_has_no_secrets(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "c")
    env = scan.scanner_env()
    assert not {"GITHUB_TOKEN", "GITHUB_WEBHOOK_SECRET", "CLAUDE_CODE_OAUTH_TOKEN"} & set(env)


# ---- real scanners (they are installed in the image) ------------------------------------

@pytest.mark.skipif(shutil.which("semgrep") is None, reason="scanners not installed")
def test_real_semgrep_and_gitleaks(tmp_path):
    ws = tmp_path / "ws"
    shutil.copytree(SAMPLE, ws)
    (ws / "ignored.py").write_text("import os\nos.system('x')  # nosemgrep\n")
    reports = tmp_path / "reports"
    reports.mkdir()
    records = []
    findings = run_scanners(ws, reports, lambda *r: records.append(r), timeout_s=120)
    status = {r[0]: r[1] for r in records}
    assert status["semgrep"] == "ok" and status["gitleaks"] == "ok"
    assert "osv-scanner" in status
    tools = {f.tool for f in findings}
    assert {"semgrep", "gitleaks"} <= tools
    assert any(f.path == "ignored.py" for f in findings), "nosemgrep must not suppress findings"


def test_osv_transitive_dependency_has_no_line(tmp_path):
    # urllib3 etc. are resolved transitively and not named in the manifest: line 0, never "in diff"
    (tmp_path / "requirements.txt").write_text("Jinja2==2.10\n")
    fs = normalize_report("osv-scanner", load("osv.sarif"), Path("/src"), tmp_path)
    assert {f.line for f in fs if "jinja2@" not in f.message.lower()} <= {0}
    assert all(f.line == 1 for f in fs if "jinja2@" in f.message.lower())
