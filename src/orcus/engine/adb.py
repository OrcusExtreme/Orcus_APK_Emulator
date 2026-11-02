"""Private adb (ARCHITECTURE.md §5.5): our own `adb -P 15037 nodaemon server`, registration, and the shells.

- AdbServer: started before the emulator, re-registers the emulator after a (re)start (S9: 0.06 s).
- RawShell: engine-internal device commands as one shell string (pipes allowed, provisioning only).
- EpochShell: the DeviceShell orcus.apk gets, bound to one epoch; argv elements are quoted one by one.
"""

import os
import shlex
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

from orcus.common import childenv, log, logtext, messages_ko
from orcus.common.paths import OrcusPaths
from orcus.contracts import CmdResult, EngineNotReady, ErrorCode, LogEntry

from .seams import LineStream, ProcessHandle, Seams

_log = log.get("engine.adb")
SLOW_CMD_S = 10.0   # an adb or guest stall worth a log line (one launch took 22 s in an acceptance run)


def client(server_port: int, serial: str | None, *args: str) -> list[str]:
    """`-H 127.0.0.1` makes a down server fail fast instead of being started behind our back (P, S9)."""
    head = ["-H", "127.0.0.1", "-P", str(server_port)]
    return [*head, "-s", serial, *args] if serial else [*head, *args]


class AdbServer:
    def __init__(self, paths: OrcusPaths, seams: Seams, port: int) -> None:
        self._paths = paths
        self._seams = seams
        self.port = port
        self._proc: ProcessHandle | None = None
        self.restarts: list[float] = []   # monotonic times, for the 3-per-5-minutes rule

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def answers(self, timeout_s: float = 5) -> bool:
        return self._seams.adb(client(self.port, None, "version"), timeout_s).code == 0

    def ensure(self, timeout_s: float = 10) -> None:
        """Start the server if ours is not running. Raises OSError/TimeoutError on failure."""
        if self.running() and self.answers():
            return
        if self._proc is not None:
            self.restarts.append(self._seams.clock.monotonic())
            self.stop()
        env = childenv.adb_server_env(self._paths, os.environ, adb_server_port=self.port,
                                      strip_dirs=childenv.app_dirs_to_strip())
        self._proc = self._seams.procs.spawn([str(self._paths.adb_exe), "-P", str(self.port), "nodaemon", "server"],
                                             env=env, log_path=self._paths.logs / "adb-server.log")
        deadline = self._seams.clock.monotonic() + timeout_s
        while self._seams.clock.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise OSError(f"adb server exited with {self._proc.poll()} (port {self.port} busy?)")
            if self.answers():
                _log.info("private adb server up on %d (pid %d)", self.port, self._proc.pid)
                return
            self._seams.clock.sleep(0.2)
        raise TimeoutError("private adb server did not answer")

    def register(self, emulator_adb_port: int) -> str:
        try:
            return self._seams.register(self.port, emulator_adb_port)
        except OSError as e:
            return f"{type(e).__name__}: {e}"

    def wait(self, timeout_s: float) -> int | None:
        """The server's exit code once it has ended; None while it runs (engine-adbwait, §4)."""
        proc = self._proc
        if proc is None:
            time.sleep(timeout_s)
            return None
        return proc.wait(timeout_s)

    def recent_restarts(self, window_s: float = 300) -> int:
        now = self._seams.clock.monotonic()
        return sum(1 for t in self.restarts if now - t <= window_s)

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait(5)
        self._proc = None


class RawShell:
    """`adb shell "<cmd>"` for engine-owned commands (provisioning, session_init, checks)."""

    def __init__(self, adb: Callable[[Sequence[str], float], CmdResult], server_port: int, serial: str) -> None:
        self._adb = adb
        self._port = server_port
        self.serial = serial

    def __call__(self, cmd: str, timeout_s: float = 30) -> CmdResult:
        return self._adb(client(self._port, self.serial, "shell", cmd), timeout_s)

    def state(self, timeout_s: float = 5) -> str:
        r = self._adb(client(self._port, self.serial, "get-state"), timeout_s)
        return r.out.strip() if r.code == 0 else ""

    def text(self, cmd: str, timeout_s: float = 30) -> str:
        r = self(cmd, timeout_s)
        return r.out.strip() if r.code == 0 else ""


class EpochShell:
    """DeviceShell for orcus.apk. `guard(epoch)` raises EngineNotReady once the epoch is gone."""

    def __init__(self, adb: Callable[[Sequence[str], float], CmdResult], server_port: int, serial: str,
                 epoch: int, guard: Callable[[int], None]) -> None:
        self._adb = adb
        self._port = server_port
        self.serial = serial
        self.epoch = epoch
        self._guard = guard

    def _run(self, timeout_s: float, *args: str) -> CmdResult:
        self._guard(self.epoch)
        r = self._adb(client(self._port, self.serial, *args), timeout_s)
        if r.elapsed_s >= SLOW_CMD_S or r.timed_out:
            _log.warning("adb %s took %.1f s (code %d%s)", " ".join(args)[:160], r.elapsed_s, r.code,
                         ", timed out" if r.timed_out else "")
        return r

    def get_state(self) -> str:
        r = self._run(10, "get-state")
        return r.out.strip() if r.code == 0 else ""

    def shell(self, argv: Sequence[str], timeout_s: float = 30) -> CmdResult:
        # adb joins its arguments with spaces; quoting each element keeps '' and spaces intact (§3)
        return self._run(timeout_s, "shell", " ".join(shlex.quote(a) for a in argv))

    def install(self, apk: Path, flags: Sequence[str], timeout_s: float = 180) -> CmdResult:
        return self._run(timeout_s, "install", *flags, str(apk))

    def uninstall(self, package: str) -> CmdResult:
        return self._run(60, "uninstall", package)

    def push(self, src: Path, dst: str, timeout_s: float = 120) -> CmdResult:
        return self._run(timeout_s, "push", str(src), dst)

    def pull(self, src: str, dst: Path, timeout_s: float = 120) -> CmdResult:
        return self._run(timeout_s, "pull", src, str(dst))


class AdbLogcat:
    """CancellableIter[LogEntry]: `adb logcat -v threadtime -T 1`, i.e. the guest's log from now on.

    gRPC streamLogcat sends nothing at all after LoadSnapshot ([Reset phone]), even after restarting the guest's
    goldfish-logcat; adb is unaffected by snapshot loads.
    """

    ARGS = ("logcat", "-v", "threadtime", "-T", "1")

    def __init__(self, lines: LineStream) -> None:
        self._lines = lines

    def __iter__(self) -> Iterator[LogEntry]:
        for line in self._lines:
            entry = logtext.parse(line)
            if entry is not None:
                yield entry

    def cancel(self) -> None:
        self._lines.close()


def not_ready(detail: str) -> EngineNotReady:
    return EngineNotReady(messages_ko.error(ErrorCode.ENGINE_UNAVAILABLE, detail))
