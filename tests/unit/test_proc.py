import sys
import time

from orcus.common import proc, winjob

PY = sys.executable


def test_normal_run_captures_utf8() -> None:
    r = proc.run([PY, "-X", "utf8", "-c", "import sys; sys.stdout.write('안녕 ok'); sys.stderr.write('err'); sys.exit(3)"],
                 timeout_s=30)
    assert (r.code, r.out, r.err, r.timed_out) == (3, "안녕 ok", "err", False)


def test_timeout_kills_and_returns() -> None:
    start = time.monotonic()
    r = proc.run([PY, "-c", "import time; time.sleep(60)"], timeout_s=1)
    assert r.timed_out and time.monotonic() - start < 10


GRANDCHILD = (
    "import subprocess, sys, time\n"
    "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "print(g.pid, flush=True)\n"
    "time.sleep(float(sys.argv[1]))\n"
)


def _kill_reported_pid(out: str) -> None:
    for token in out.split():
        if token.isdigit():
            winjob.terminate_pid(int(token))


def test_grandchild_holding_the_pipe_does_not_hang_a_finished_run() -> None:
    start = time.monotonic()
    r = proc.run([PY, "-c", GRANDCHILD, "0"], timeout_s=30)
    try:
        assert not r.timed_out and r.code == 0
        assert time.monotonic() - start < proc.GRACE_AFTER_EXIT_S + 8
    finally:
        _kill_reported_pid(r.out)


def test_grandchild_holding_the_pipe_does_not_hang_a_timeout() -> None:
    start = time.monotonic()
    r = proc.run([PY, "-c", GRANDCHILD, "60"], timeout_s=2)
    try:
        assert r.timed_out
        assert time.monotonic() - start < 12
    finally:
        _kill_reported_pid(r.out)


def test_input_text() -> None:
    r = proc.run([PY, "-X", "utf8", "-c", "import sys; print(sys.stdin.read().upper())"], timeout_s=30, input_text="abc")
    assert r.out.strip() == "ABC"
