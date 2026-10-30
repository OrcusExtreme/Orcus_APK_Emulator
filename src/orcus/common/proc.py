"""Child process runner with a guaranteed timeout (ARCHITECTURE.md §5.5).

subprocess.run(timeout=...) with PIPE is not used on Windows: after killing the child CPython calls
communicate() without a timeout, which hangs while a grandchild still holds the pipe. Here reader
threads drain the pipes and are abandoned (daemon) if they do not finish in time.
"""

import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO

from orcus.contracts import CmdResult

CREATE_NO_WINDOW = 0x08000000
GRACE_AFTER_EXIT_S = 2.0


def _pump(stream: IO[bytes], sink: list[bytes]) -> None:
    try:
        while chunk := stream.read1(65536):   # type: ignore[attr-defined]
            sink.append(chunk)
    except (OSError, ValueError):
        pass


def _decode(chunks: list[bytes]) -> str:
    return b"".join(list(chunks)).decode("utf-8", errors="replace")


def run(argv: Sequence[str], *, timeout_s: float, env: Mapping[str, str] | None = None,
        cwd: Path | None = None, input_text: str | None = None) -> CmdResult:
    """Run to completion or kill at `timeout_s`. Never raises for a non-zero exit code.

    Raises OSError only when the program cannot be started at all.
    """
    start = time.monotonic()
    proc = subprocess.Popen(
        list(argv),
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=dict(env) if env is not None else None, cwd=cwd, creationflags=CREATE_NO_WINDOW)
    out: list[bytes] = []
    err: list[bytes] = []
    readers = [threading.Thread(target=_pump, args=(proc.stdout, out), name="proc-out", daemon=True),
               threading.Thread(target=_pump, args=(proc.stderr, err), name="proc-err", daemon=True)]
    for t in readers:
        t.start()
    if input_text is not None and proc.stdin is not None:
        try:
            proc.stdin.write(input_text.encode("utf-8"))
            proc.stdin.close()
        except OSError:
            pass

    timed_out = False
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()   # TerminateProcess
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    grace_end = time.monotonic() + (0.5 if timed_out else GRACE_AFTER_EXIT_S)
    for t in readers:
        t.join(max(0.0, grace_end - time.monotonic()))
    code = proc.returncode if proc.returncode is not None else -1
    return CmdResult(argv=tuple(str(a) for a in argv), code=code, out=_decode(out), err=_decode(err),
                     elapsed_s=time.monotonic() - start, timed_out=timed_out)


def spawn(argv: Sequence[str], *, env: Mapping[str, str], log_path: Path,
          cwd: Path | None = None) -> subprocess.Popen[bytes]:
    """Start a long-lived child (adb server, emulator) with output appended to `log_path`.

    No pipes: such children may start grandchildren that would inherit them.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log:
        return subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                env=dict(env), cwd=cwd, creationflags=CREATE_NO_WINDOW)
