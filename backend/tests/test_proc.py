import sys
import threading
import time

import pytest

from app.pipeline import proc
from app.pipeline.proc import ProcCancelled, ProcTimeout, base_env, run_proc


def py(code):
    return [sys.executable, "-c", code]


def test_run_proc_ok(tmp_path):
    r = run_proc(py("import sys; print(sys.stdin.read().upper())"), tmp_path, base_env(), 10, input_text="hi")
    assert r.returncode == 0 and r.stdout.strip() == "HI"


def test_run_proc_timeout(tmp_path):
    with pytest.raises(ProcTimeout):
        run_proc(py("import time; time.sleep(30)"), tmp_path, base_env(), 0.5)


def test_cancel_kills_running_process_quickly(tmp_path):
    key = "run-cancel-1"
    errors = []

    def target():
        try:
            run_proc(py("import time; time.sleep(30)"), tmp_path, base_env(), 60, key=key)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=target)
    t.start()
    deadline = time.time() + 5
    while not proc.is_running(key):
        assert time.time() < deadline
        time.sleep(0.05)
    start = time.time()
    assert proc.cancel(key) == 1
    t.join(5)
    assert not t.is_alive() and time.time() - start < 5
    assert isinstance(errors[0], ProcCancelled)


def test_cancelled_key_refuses_new_processes(tmp_path):
    proc.cancel("run-cancel-2")
    with pytest.raises(ProcCancelled):
        run_proc(py("print(1)"), tmp_path, base_env(), 10, key="run-cancel-2")


def test_unrelated_keys_unaffected(tmp_path):
    proc.cancel("run-cancel-3")
    assert run_proc(py("print(1)"), tmp_path, base_env(), 10, key="run-other").stdout.strip() == "1"
