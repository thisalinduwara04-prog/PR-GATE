"""Subprocess helper: no shell, own process group, whole group killed on timeout or cancel.

Processes can be registered under a key (the run id) so a superseded run can be cancelled (B6):
cancel(key) kills every process group running for that key and refuses new ones for it.
"""

import os
import signal
import subprocess
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

MAX_CANCELLED_KEYS = 1000


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str


class ProcTimeout(Exception):
    pass


class ProcCancelled(Exception):
    pass


_lock = threading.Lock()
_running: dict[str, set[subprocess.Popen]] = {}
_cancelled: "OrderedDict[str, None]" = OrderedDict()


def _kill_group(p: subprocess.Popen) -> None:
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def is_running(key: str) -> bool:
    with _lock:
        return bool(_running.get(key))


def cancel(key: str) -> int:
    """Kill everything running under `key` and refuse new processes for it. Returns how many were killed."""
    with _lock:
        _cancelled[key] = None
        _cancelled.move_to_end(key)
        while len(_cancelled) > MAX_CANCELLED_KEYS:
            _cancelled.popitem(last=False)
        procs = list(_running.get(key, ()))
    for p in procs:
        _kill_group(p)
    return len(procs)


def run_proc(argv: list[str], cwd: Path, env: dict[str, str], timeout_s: float,
             input_text: str | None = None, key: str | None = None) -> ProcResult:
    with _lock:
        if key is not None and key in _cancelled:
            raise ProcCancelled(f"{key} was cancelled")
        proc = subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL if input_text is None else subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
        if key is not None:
            _running.setdefault(key, set()).add(proc)
    try:
        try:
            out, err = proc.communicate(input=input_text, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            proc.communicate()
            raise ProcTimeout(f"timed out after {timeout_s}s") from None
    finally:
        if key is not None:
            with _lock:
                _running.get(key, set()).discard(proc)
                if not _running.get(key):
                    _running.pop(key, None)
    if key is not None:
        with _lock:
            if key in _cancelled:
                raise ProcCancelled(f"{key} was cancelled")
    return ProcResult(proc.returncode, out, err)


def base_env(**extra: str) -> dict[str, str]:
    """Minimal child environment: never inherits tokens or secrets."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C.UTF-8",
    }
    env.update(extra)
    return env
