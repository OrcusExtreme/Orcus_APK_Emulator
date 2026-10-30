"""Engine seams (ARCHITECTURE.md §2): everything EngineManager touches outside Python, injectable.

The state machine only talks to these, so the unit tests drive it with fakes (FakeEmulatorServer for gRPC,
scripted adb, fake processes) and the real wiring lives in `Seams.real()`.
"""

import os
import socket
import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import grpc

from orcus.common import accel, childenv, hostinfo, ports, proc, winjob
from orcus.common import paths as paths_mod
from orcus.common.clock import Clock, SystemClock
from orcus.common.paths import OrcusPaths
from orcus.common.ports import Ports
from orcus.contracts import CmdResult, PortSettings
from orcus.emugrpc import channel as grpc_channel

from . import discovery
from .discovery import DiscoveryRecord


class ProcessHandle(Protocol):
    @property
    def pid(self) -> int: ...
    def poll(self) -> int | None: ...
    def wait(self, timeout_s: float) -> int | None: ...   # exit code, or None when still running
    def kill(self) -> None: ...


class ProcessHost(Protocol):
    def spawn(self, argv: Sequence[str], *, env: Mapping[str, str], log_path: Path) -> ProcessHandle: ...
    def sdk_processes(self) -> list[tuple[int, str]]: ...   # (pid, image) of images under the SDK folder
    def terminate(self, pid: int) -> bool: ...
    def alive(self, pid: int) -> bool: ...


class DiscoverySource(Protocol):
    def find(self, serial_port: int) -> DiscoveryRecord | None: ...
    def ours(self) -> list[DiscoveryRecord]: ...


class AdbRunner(Protocol):
    """An adb client call: argv after `adb.exe`, run with the engine's child environment."""

    def __call__(self, argv: Sequence[str], timeout_s: float) -> CmdResult: ...


class LineStream(Protocol):
    """Output lines of a long-running adb client (`adb logcat`); close() ends the process."""

    def __iter__(self) -> Iterator[str]: ...
    def close(self) -> None: ...


class AdbLines(Protocol):
    def __call__(self, argv: Sequence[str]) -> LineStream: ...


class PortProber(Protocol):
    def choose(self, preferred: PortSettings) -> Ports: ...
    def save(self, chosen: Ports) -> None: ...


type ChannelFactory = Callable[[int, str], grpc.Channel]


@dataclass(frozen=True)
class Seams:
    procs: ProcessHost
    discovery: DiscoverySource
    adb: AdbRunner
    register: Callable[[int, int], str]   # (adb server port, emulator adb port) -> adb's reply
    ports: PortProber
    clock: Clock
    channel: ChannelFactory
    whpx_on: Callable[[], bool]
    disk_free: Callable[[Path], int]      # bytes free on the drive holding the path
    env: Callable[[int], dict[str, str]]  # child environment for an adb server port
    home_problems: Callable[[Path], list[str]] = paths_mod.home_problems   # root guard (§11.4)
    adb_lines: AdbLines | None = None     # `adb logcat` for the app; None: the session's gRPC stream only

    @staticmethod
    def real(paths: OrcusPaths) -> "Seams":
        def env(adb_server_port: int) -> dict[str, str]:
            return childenv.base_env(paths, os.environ, adb_server_port=adb_server_port,
                                     strip_dirs=childenv.app_dirs_to_strip())

        return Seams(procs=_RealProcesses(paths), discovery=_RealDiscovery(paths),
                     adb=_RealAdb(paths, env), register=register_emulator, ports=_RealPorts(paths),
                     clock=SystemClock(), channel=grpc_channel.make_channel,
                     whpx_on=lambda: accel.query_install_state() == accel.WhpxState.ENABLED,
                     disk_free=hostinfo.disk_free, env=env, adb_lines=_RealAdbLines(paths, env))


def register_emulator(adb_server_port: int, emulator_adb_port: int, timeout_s: float = 5.0) -> str:
    """adb's own registration message `host:emulator:<port>` (§5.5): the server connects at once."""
    payload = f"host:emulator:{emulator_adb_port}".encode()
    with socket.create_connection(("127.0.0.1", adb_server_port), timeout=timeout_s) as s:
        s.sendall(f"{len(payload):04x}".encode() + payload)
        try:
            return s.recv(64).decode(errors="replace")
        except TimeoutError:
            return ""


class _Popen:
    def __init__(self, p: subprocess.Popen[bytes]) -> None:
        self._p = p

    @property
    def pid(self) -> int:
        return self._p.pid

    def poll(self) -> int | None:
        return self._p.poll()

    def wait(self, timeout_s: float) -> int | None:
        try:
            return self._p.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return None

    def kill(self) -> None:
        self._p.kill()


class _RealProcesses:
    def __init__(self, paths: OrcusPaths) -> None:
        self._paths = paths

    def spawn(self, argv: Sequence[str], *, env: Mapping[str, str], log_path: Path) -> ProcessHandle:
        return _Popen(proc.spawn(argv, env=env, log_path=log_path))

    def sdk_processes(self) -> list[tuple[int, str]]:
        return winjob.processes_under(self._paths.sdk)

    def terminate(self, pid: int) -> bool:
        return winjob.terminate_pid(pid)

    def alive(self, pid: int) -> bool:
        return winjob.pid_alive(pid)


class _RealDiscovery:
    def __init__(self, paths: OrcusPaths) -> None:
        self._paths = paths
        self._dirs = discovery.candidate_dirs({"LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
                                               "TMP": str(paths.tmp)})

    def find(self, serial_port: int) -> DiscoveryRecord | None:
        return discovery.find(self._dirs, serial_port=serial_port, avd_dir=self._paths.avd_data,
                              alive=winjob.pid_alive)

    def ours(self) -> list[DiscoveryRecord]:
        return discovery.ours(self._dirs, avd_dir=self._paths.avd_data)


class _RealAdb:
    def __init__(self, paths: OrcusPaths, env: Callable[[int], dict[str, str]]) -> None:
        self._paths = paths
        self._env = env

    def __call__(self, argv: Sequence[str], timeout_s: float) -> CmdResult:
        port = int(argv[argv.index("-P") + 1]) if "-P" in argv else 15037
        try:
            return proc.run([str(self._paths.adb_exe), *argv], timeout_s=timeout_s, env=self._env(port))
        except OSError as e:   # adb.exe missing or blocked: same shape as a failed call
            return CmdResult(tuple(argv), -1, "", f"{type(e).__name__}: {e}", 0.0, False)


class _PipeLines:
    def __init__(self, p: subprocess.Popen[bytes]) -> None:
        self._p = p

    def __iter__(self) -> Iterator[str]:
        assert self._p.stdout is not None
        for raw in self._p.stdout:
            yield raw.decode("utf-8", errors="replace").rstrip("\r\n")

    def close(self) -> None:
        if self._p.poll() is None:
            self._p.kill()
        try:
            self._p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        if self._p.stdout is not None:
            self._p.stdout.close()


class _RealAdbLines:
    def __init__(self, paths: OrcusPaths, env: Callable[[int], dict[str, str]]) -> None:
        self._paths = paths
        self._env = env

    def __call__(self, argv: Sequence[str]) -> LineStream:
        port = int(argv[argv.index("-P") + 1]) if "-P" in argv else 15037
        p = subprocess.Popen([str(self._paths.adb_exe), *argv], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, env=self._env(port),
                             creationflags=subprocess.CREATE_NO_WINDOW)
        return _PipeLines(p)


class _RealPorts:
    def __init__(self, paths: OrcusPaths) -> None:
        self._paths = paths

    def choose(self, preferred: PortSettings) -> Ports:
        return ports.choose(preferred, ports.load_saved(self._paths))

    def save(self, chosen: Ports) -> None:
        ports.save(self._paths, chosen)
