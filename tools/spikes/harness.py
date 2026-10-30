"""Throwaway spike harness (Phase 2, not packaged): one live emulator session for S6/S9/S2/S1/S4.

The real engine (state machine, golden lifecycle) is Phase 3a. This is the minimum needed to ask the
spike questions: our KILL_ON_JOB_CLOSE job, the engine lock, a private `adb nodaemon server`, the
emulator with a fixed command line, discovery, the token channel, adb shell, and a clean stop.

    from harness import Live
    with Live(mode=LaunchMode.COLD) as live:
        print(live.shell("getprop", "ro.build.version.sdk").out)
"""

import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

sys.dont_write_bytecode = True
SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import grpc  # noqa: E402
from google.protobuf import empty_pb2  # noqa: E402

from orcus.app import compose  # noqa: E402
from orcus.common import childenv, ports, proc, winjob  # noqa: E402
from orcus.common.paths import OrcusPaths  # noqa: E402
from orcus.contracts import CmdResult, GpuMode, Settings  # noqa: E402
from orcus.emugrpc import channel as grpc_channel  # noqa: E402
from orcus.emugrpc.generated import emulator_controller_pb2 as ec  # noqa: E402
from orcus.engine import args, discovery  # noqa: E402
from orcus.engine.args import LaunchMode  # noqa: E402

LaunchMode = LaunchMode


def say(*parts: object) -> None:
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


def register_emulator(adb_server_port: int, emulator_adb_port: int, timeout_s: float = 5.0) -> str:
    """Send `host:emulator:<port>` to the adb server, like the emulator does at start (adb smart socket)."""
    payload = f"host:emulator:{emulator_adb_port}".encode()
    with socket.create_connection(("127.0.0.1", adb_server_port), timeout=timeout_s) as s:
        s.sendall(f"{len(payload):04x}".encode() + payload)
        try:
            return s.recv(64).decode(errors="replace")
        except TimeoutError:
            return "(no reply)"


@dataclass
class Live:
    mode: LaunchMode = LaunchMode.COLD
    gpu: GpuMode = GpuMode.HOST
    keep_slot: str | None = None
    extra_args: tuple[str, ...] = ()
    start_adb: bool = True
    boot_timeout_s: float = 300.0
    home: Path = Path(r"D:\OrcusAPK")
    timings: dict[str, float] = field(default_factory=dict)
    # spike experiments only: rewrite the argv after args.build (bypasses its invariants on purpose)
    argv_transform: "Callable[[list[str]], list[str]] | None" = None

    def __post_init__(self) -> None:
        self.paths = OrcusPaths(self.home)
        self.settings: Settings = compose.load_settings(self.paths)
        self.lock = winjob.ExclusiveLock(self.paths.state_file("engine.lock"))
        self.adb_server: subprocess.Popen[bytes] | None = None
        self.emulator: subprocess.Popen[bytes] | None = None
        self.channel: grpc.Channel | None = None
        self.stub = None
        self.snap = None
        self.record: discovery.DiscoveryRecord | None = None

    # lifecycle --------------------------------------------------------------------------------
    def __enter__(self) -> "Live":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def start(self) -> None:
        """On any failure the session is torn down (emulator, adb server, lock) before re-raising."""
        compose.ensure_job()
        self.lock.acquire()
        try:
            self.ports = ports.choose(self.settings.engine.ports, ports.load_saved(self.paths))
            self.serial = self.ports.serial
            t0 = time.monotonic()
            if self.start_adb:
                self.start_adb_server()
            self.launch()
            self.connect()
            self.timings["connect"] = round(time.monotonic() - t0, 2)
            self.wait_booted()
            self.timings["booted"] = round(time.monotonic() - t0, 2)
        except BaseException:
            self.stop()
            raise

    def start_adb_server(self) -> None:
        env = childenv.adb_server_env(self.paths, os.environ, adb_server_port=self.ports.adb_server,
                                      strip_dirs=childenv.app_dirs_to_strip())
        self.adb_server = proc.spawn([str(self.paths.adb_exe), "-P", str(self.ports.adb_server), "nodaemon", "server"],
                                     env=env, log_path=self.paths.logs / "adb-server.log")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.adb("version", timeout_s=5).code == 0:
                return
            time.sleep(0.2)
        raise RuntimeError("private adb server did not answer")

    def launch(self) -> None:
        cfg = args.LaunchConfig(self.paths.emulator_exe, self.ports.console, self.ports.grpc,
                                self.settings.engine.idle_grpc_timeout_s, self.settings.engine.timezone,
                                self.settings.engine.locale, childenv.decide_proxy(os.environ).http_proxy_flag)
        self.argv = [*args.build(cfg, self.mode, self.gpu, keep_slot=self.keep_slot), *self.extra_args]
        if self.argv_transform is not None:
            self.argv = self.argv_transform(self.argv)
        env = childenv.base_env(self.paths, os.environ, adb_server_port=self.ports.adb_server,
                                strip_dirs=childenv.app_dirs_to_strip())
        log = self.paths.logs / f"emulator-spike-{time.strftime('%Y%m%d-%H%M%S')}.log"
        self.emulator_log = log
        say("launch", " ".join(self.argv[1:]))
        self.emulator = proc.spawn(self.argv, env=env, log_path=log)

    def connect(self, timeout_s: float = 90) -> None:
        dirs = discovery.candidate_dirs({"LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""), "TMP": str(self.paths.tmp)})
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            assert self.emulator is not None
            if self.emulator.poll() is not None:
                raise RuntimeError(f"emulator exited ({self.emulator.returncode}); see {self.emulator_log}")
            rec = discovery.find(dirs, serial_port=self.ports.console, avd_dir=self.paths.avd_data,
                                 alive=winjob.pid_alive)
            if rec is not None and rec.grpc_token and rec.grpc_port:
                self.record = rec
                self.channel = grpc_channel.make_channel(rec.grpc_port, rec.grpc_token)
                self.stub = grpc_channel.controller(self.channel)
                self.snap = grpc_channel.snapshots(self.channel)
                return
            time.sleep(0.25)
        raise TimeoutError("no discovery file")

    def status(self) -> ec.EmulatorStatus:
        assert self.stub is not None
        return self.stub.getStatus(empty_pb2.Empty(), timeout=5)

    def wait_booted(self) -> None:
        deadline = time.monotonic() + self.boot_timeout_s
        while time.monotonic() < deadline:
            try:
                if self.status().booted:
                    break
            except grpc.RpcError:
                pass
            time.sleep(0.5)
        else:
            raise TimeoutError("not booted")
        if self.start_adb:
            while time.monotonic() < deadline:
                if self.adb("get-state", timeout_s=5).out.strip() == "device" and \
                        self.shell("getprop", "sys.boot_completed").out.strip() == "1":
                    return
                time.sleep(0.5)
            raise TimeoutError("adb never reported a booted device")

    def stop(self, grace_s: float = 15.0) -> None:
        try:
            if self.stub is not None and self.emulator is not None and self.emulator.poll() is None:
                try:
                    self.stub.setVmState(ec.VmRunState(state=ec.VmRunState.SHUTDOWN), timeout=5)
                except grpc.RpcError as e:
                    say("SHUTDOWN failed:", e.code())
            if self.emulator is not None:
                try:
                    self.emulator.wait(timeout=grace_s)
                except subprocess.TimeoutExpired:
                    for pid, image in winjob.processes_under(self.paths.sdk):
                        if "qemu-system" in image.lower():
                            winjob.terminate_pid(pid)
                    self.emulator.kill()
            if self.channel is not None:
                self.channel.close()
        finally:
            if self.adb_server is not None and self.adb_server.poll() is None:
                self.adb_server.kill()
            left = winjob.processes_under(self.paths.sdk)
            deadline = time.monotonic() + 10
            while left and time.monotonic() < deadline:
                time.sleep(0.5)
                left = winjob.processes_under(self.paths.sdk)
            if left:
                say("leftover SDK processes after stop:", left)
                for pid, _ in left:
                    winjob.terminate_pid(pid)
            if self.lock.held:
                self.lock.release()

    # adb ----------------------------------------------------------------------------------------
    def adb(self, *argv: str, timeout_s: float = 30, serial: bool = True) -> CmdResult:
        env = childenv.base_env(self.paths, os.environ, adb_server_port=self.ports.adb_server,
                                strip_dirs=childenv.app_dirs_to_strip())
        target = ["-s", self.serial] if serial and argv and argv[0] not in ("version", "devices", "kill-server",
                                                                               "start-server") else []
        return proc.run([str(self.paths.adb_exe), "-H", "127.0.0.1", "-P", str(self.ports.adb_server), *target, *argv],
                        timeout_s=timeout_s, env=env)

    def shell(self, *argv: str, timeout_s: float = 30) -> CmdResult:
        import shlex
        return self.adb("shell", " ".join(shlex.quote(a) for a in argv), timeout_s=timeout_s)
