import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from orcus.common import proc, winjob
from orcus.common.winjob import ExclusiveLock, LockBusy

PY = sys.executable


def test_lock_is_exclusive(tmp_path: Path) -> None:
    path = tmp_path / "state" / "engine.lock"
    first = ExclusiveLock(path)
    first.acquire()
    try:
        with pytest.raises(LockBusy):
            ExclusiveLock(path).acquire()
    finally:
        first.release()
    with ExclusiveLock(path) as again:
        assert again.held


def test_lock_released_when_the_holder_dies(tmp_path: Path) -> None:
    path = tmp_path / "engine.lock"
    holder = subprocess.Popen([PY, "-c", "import sys, time; sys.path[:0] = sys.argv[2:]\n"
                               "from orcus.common.winjob import ExclusiveLock\n"
                               "ExclusiveLock(__import__('pathlib').Path(sys.argv[1])).acquire()\n"
                               "print('held', flush=True); time.sleep(60)", str(path), *sys.path],
                              stdout=subprocess.PIPE, creationflags=proc.CREATE_NO_WINDOW)
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == b"held"
        with pytest.raises(LockBusy):
            ExclusiveLock(path).acquire()
    finally:
        holder.kill()   # in a venv this is the launcher; the interpreter child dies with its job shortly after
        holder.wait(10)
    deadline = time.monotonic() + 10
    while True:
        try:
            with ExclusiveLock(path):
                break
        except LockBusy:
            assert time.monotonic() < deadline, "lock still held 10 s after the holder was killed"
            time.sleep(0.1)


JOB_SCRIPT = """
import json, subprocess, sys, time
sys.path[:0] = sys.argv[1:]
from orcus.common import winjob
job = winjob.Job.create(kill_on_close=True)
job.assign_current()
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
time.sleep(0.5)
ids = job.process_ids()
print(json.dumps({"child": child.pid, "ids": ids, "me": __import__('os').getpid()}), flush=True)
"""


def test_kill_on_job_close_takes_children_down() -> None:
    r = proc.run([PY, "-c", JOB_SCRIPT, *sys.path], timeout_s=30)
    assert r.code == 0, r.err
    info = json.loads(r.out.strip().splitlines()[-1])
    assert info["child"] in info["ids"] and info["me"] in info["ids"]
    deadline = time.monotonic() + 10
    while winjob.pid_alive(info["child"]) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not winjob.pid_alive(info["child"]), "child survived the job handle closing"


def test_process_inspection() -> None:
    p = subprocess.Popen([PY, "-c", "import time; time.sleep(30)"], creationflags=proc.CREATE_NO_WINDOW)
    try:
        time.sleep(0.5)
        assert winjob.pid_alive(p.pid)
        image = winjob.image_path(p.pid)
        assert image is not None and image.lower().endswith("python.exe")
        under = winjob.processes_under(Path(image).parent)
        assert p.pid in [pid for pid, _ in under]
        assert any(e.pid == p.pid for e in winjob.list_processes())
    finally:
        assert winjob.terminate_pid(p.pid)
    assert not winjob.pid_alive(p.pid)


def test_is_process_in_job_for_self() -> None:
    assert isinstance(winjob.is_process_in_job(winjob.kernel32.GetCurrentProcess()), bool)
