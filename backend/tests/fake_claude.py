"""Builds fake `claude` executables for deterministic verify tests.

Each fake reads the prompt from stdin, pulls the finding ids out of the findings block,
records argv/cwd/env to a JSON file, and prints a CLI-shaped JSON result for its mode.
"""

import stat
import sys
from pathlib import Path

TEMPLATE = r'''#!{python}
import json, os, re, sys, time
MODE = {mode!r}
prompt = sys.stdin.read()
json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd(), "env": dict(os.environ), "prompt": prompt}},
          open({record!r}, "w"))
m = re.search(r"<findings-(\w+)>\n(.*?)\n</findings-\1>", prompt, re.S)
ids = [f["id"] for f in json.loads(m.group(2))] if m else []
results = [{{"finding_id": i, "verdict": "confirmed", "justification": "reachable"}} for i in ids]
if MODE == "mixed":
    verdicts = ["confirmed", "false_positive", "unclear"]
    results = [{{"finding_id": i, "verdict": verdicts[n % 3], "justification": "j" + i}} for n, i in enumerate(ids)]
if MODE == "sleep":
    time.sleep(60)
if MODE == "cli_error":
    print(json.dumps({{"type": "result", "subtype": "error_max_turns", "is_error": True}})); sys.exit(1)
if MODE == "api_error":
    print(json.dumps({{"type": "result", "subtype": "success", "is_error": True, "api_error_status": 429,
                      "terminal_reason": "api_error", "result": "API Error: rate limited"}})); sys.exit(1)
if MODE == "flaky":
    counter = {record!r} + ".count"
    n = int(open(counter).read()) if os.path.exists(counter) else 0
    open(counter, "w").write(str(n + 1))
    if n == 0:
        print(json.dumps({{"type": "result", "subtype": "success", "is_error": True, "terminal_reason": "api_error"}})); sys.exit(1)
if MODE == "not_json":
    print("hello"); sys.exit(0)
if MODE == "missing":
    results = results[1:]
if MODE == "duplicate":
    results = results + results[:1]
if MODE == "unknown":
    results = results + [{{"finding_id": "F999", "verdict": "confirmed", "justification": "x"}}]
if MODE == "bad_verdict":
    results[0]["verdict"] = "definitely"
out = {{"results": results}}
if MODE == "extra_key":
    out["note"] = "hi"
payload = {{"type": "result", "subtype": "success", "is_error": False, "result": json.dumps(out)}}
if MODE != "result_only":
    payload["structured_output"] = out
print(json.dumps(payload))
'''


def make_fake_claude(dir_: Path, mode: str) -> tuple[Path, Path]:
    """Returns (executable_path, record_path)."""
    exe = dir_ / f"claude-{mode}"
    record = dir_ / f"claude-{mode}.record.json"
    exe.write_text(TEMPLATE.format(python=sys.executable, mode=mode, record=str(record)))
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return exe, record
