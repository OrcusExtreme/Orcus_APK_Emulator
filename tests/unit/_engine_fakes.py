"""Fakes for the EngineManager tests (ARCHITECTURE.md §12.2): a whole emulator world around FakeEmulatorServer.

FakeDevice models what the state machine depends on: settings persist in userdata (unless wiped or not flushed),
RAM properties (debug.*) vanish on a cold boot, and a golden load brings back the properties saved with it.
FastClock makes every engine sleep instant, so 30 s waits cost nothing.
"""

import itertools
import os
import shlex
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from orcus.common.paths import GOLDEN_SNAPSHOT, OrcusPaths
from orcus.common.ports import Ports
from orcus.contracts import CmdResult, DeviceEndpoint, PortSettings, Settings
from orcus.emugrpc.channel import make_channel
from orcus.emugrpc.fake import FakeEmulatorServer
from orcus.emugrpc.generated import emulator_controller_pb2 as ec
from orcus.engine.discovery import DiscoveryRecord
from orcus.engine.manager import EngineManager
from orcus.engine.seams import Seams

ACCEL_LINE = "INFO | Windows Hypervisor Platform accelerator is operational\n"
GLES_HOST = "GLES: Google (NVIDIA Corporation), Android Emulator OpenGL ES Translator (NVIDIA GeForce GTX 1660)"
GLES_SOFT = "GLES: Google (Google Inc.), Android Emulator OpenGL ES Translator (Google SwiftShader)"
GIB = 1 << 30


class FastClock:
    def __init__(self) -> None:
        self._offset = 0.0
        self._lock = threading.Lock()

    def monotonic(self) -> float:
        return time.monotonic() + self._offset

    def time(self) -> float:
        return time.time() + self._offset

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self._offset += max(0.0, seconds)
        time.sleep(0.001)


class FakeDevice:
    RAM_DEFAULTS = {"sys.boot_completed": "1", "ro.build.version.sdk": "36",
                    "ro.product.cpu.abilist": "x86_64,arm64-v8a", "sys.system_server.start_count": "1"}

    def __init__(self) -> None:
        self.settings: dict[tuple[str, str], str] = {}
        self.persist = {"persist.sys.locale": "en-US", "persist.sys.timezone": "Asia/Seoul"}
        self.props: dict[str, str] = dict(self.RAM_DEFAULTS)
        self.golden_props: dict[str, str] | None = None
        self.unflushed: dict[tuple[str, str], str] = {}
        self.flushed = True
        self.renderer = GLES_HOST
        self.restart_pending = False
        self.adb_ok = True
        self.never_persist: set[tuple[str, str]] = set()   # settings that "do not read back"
        self.drop_unflushed_on_power_off = True
        self.commands: list[str] = []
        self.lock = threading.Lock()
        self.framework_up = True          # system_server alive, package and power services answer
        self.dead_golden = False          # the saved golden comes back without its framework (Phase 3a)
        self.kill_framework_on_clean_boot = False
        self.load = 0.4                   # /proc/loadavg
        self.busy = 0.1                   # share of CPU time not idle (/proc/stat)

    # lifecycle ------------------------------------------------------------------------------------------------
    def power_off(self) -> None:
        if self.drop_unflushed_on_power_off:
            for k in self.unflushed:
                self.settings.pop(k, None)
        self.unflushed.clear()

    def cold_boot(self, *, wipe: bool, change_locale: str | None) -> None:
        if wipe:
            self.settings.clear()
        self.props = dict(self.RAM_DEFAULTS)
        self.restart_pending = change_locale is not None
        if change_locale:
            self.persist["persist.sys.locale"] = change_locale
        self.framework_up = not (self.kill_framework_on_clean_boot and change_locale is None)

    def save_golden(self) -> None:
        self.golden_props = dict(self.props)
        self.dead_golden = not self.framework_up

    def load_golden(self) -> bool:
        if self.golden_props is None:
            return False
        self.props = dict(self.golden_props)
        self.framework_up = not self.dead_golden
        return True

    # shell ----------------------------------------------------------------------------------------------------
    def shell(self, cmd: str) -> tuple[int, str]:
        with self.lock:
            self.commands.append(cmd)
            return self._shell(cmd)

    def _get_setting(self, ns: str, key: str) -> str:
        return self.settings.get((ns, key), "null")

    def _put_setting(self, ns: str, key: str, value: str) -> None:
        if (ns, key) in self.never_persist:
            return
        self.settings[(ns, key)] = value
        self.unflushed[(ns, key)] = value

    def _shell(self, cmd: str) -> tuple[int, str]:
        p = shlex.split(cmd) if "|" not in cmd and ";" not in cmd else cmd.split()
        if cmd == "sync; echo synced":
            self.unflushed.clear()
            return 0, "synced"
        if cmd.startswith("service check"):
            return 0, "\n".join(f"Service {s}: found" for s in cmd.split("service check ")[1:])
        if cmd == "dumpsys SurfaceFlinger | grep -m1 GLES":
            return 0, self.renderer
        if cmd == "dumpsys power | grep -m1 mStayOn=":
            return 0, f"mStayOn={self.settings.get(('svc', 'stayon'), 'false')}"
        if p[:2] == ["getprop", "sys.system_server.start_count"] and self.restart_pending:
            self.restart_pending = False
            value = self.props["sys.system_server.start_count"]
            self.props["sys.system_server.start_count"] = str(int(value) + 1)
            return 0, value
        if p[0] == "getprop" and len(p) == 2:
            return 0, self.props.get(p[1], self.persist.get(p[1], ""))
        if p[0] == "setprop" and len(p) >= 2:
            self.props[p[1]] = p[2] if len(p) > 2 else ""
            return 0, ""
        if p[:2] == ["settings", "put"]:
            self._put_setting(p[2], p[3], p[4])
            return 0, ""
        if p[:2] == ["settings", "get"]:
            return 0, self._get_setting(p[2], p[3])
        if cmd == "svc power stayon true":
            self._put_setting("svc", "stayon", "true")
            return 0, ""
        if cmd == "locksettings set-disabled true":
            self._put_setting("lock", "disabled", "true")
            return 0, ""
        if cmd == "locksettings get-disabled":
            return 0, self._get_setting("lock", "disabled")
        if cmd == "cmd location set-location-enabled true":
            self._put_setting("loc", "enabled", "true")
            return 0, ""
        if cmd == "cmd location is-location-enabled":
            return 0, self._get_setting("loc", "enabled")
        if cmd.startswith("cmd overlay enable-exclusive"):
            self._put_setting("secure", "navigation_mode", "0")
            return 0, ""
        if cmd == "cmd bluetooth_manager disable":
            self._put_setting("global", "bluetooth_on", "0")
            return 0, ""
        if cmd == "pidof system_server":
            return (0, "691") if self.framework_up else (1, "")
        if cmd == "cmd package list packages android":
            return (0, "package:android\npackage:com.android.systemui") if self.framework_up else \
                (224, "cmd: Failure calling service package: Broken pipe (32)")
        if cmd == "dumpsys power | grep -m1 mWakefulness=":
            return (0, "  mWakefulness=Awake") if self.framework_up else (0, "")
        if cmd == "cat /proc/loadavg":
            return 0, f"{self.load:.2f} 0.50 0.40 1/300 1234"
        if cmd.startswith("head -1 /proc/stat; sleep"):
            busy = int(1000 * self.busy)
            return 0, f"cpu  100 0 100 800 0 0 0 0 0 0\ncpu  {100 + busy} 0 100 {800 + 1000 - busy} 0 0 0 0 0 0"
        if cmd == "date +%s":
            return 0, str(int(time.time()))
        if cmd == "echo ok":
            return 0, "ok"
        if "TTS_SERVICE" in cmd or "RECOGNIZE_SPEECH" in cmd or "RecognitionService" in cmd:
            return 0, "packageName=com.google.android.tts"
        if cmd == "ime list -s":
            return 0, "com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME"
        return 0, ""


class _HookSet(set[str]):
    def __init__(self, env: "EngineEnv") -> None:
        super().__init__()
        self._env = env

    def add(self, element: str, /) -> None:
        super().add(element)
        if element == GOLDEN_SNAPSHOT:
            self._env.device.save_golden()
            self._env.sync_golden_file()

    def discard(self, element: object, /) -> None:
        super().discard(element)
        if element == GOLDEN_SNAPSHOT:
            self._env.device.golden_props = None
            self._env.sync_golden_file()


class HookedFake(FakeEmulatorServer):
    def __init__(self, env: "EngineEnv") -> None:
        super().__init__()
        self.env = env
        self.snapshots = _HookSet(env)

    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        if name == "loaded" and value == GOLDEN_SNAPSHOT and hasattr(self, "env"):
            self.env.device.load_golden()


class FakeProc:
    def __init__(self, pid: int, exit_when: Callable[[], bool] = lambda: False) -> None:
        self.pid = pid
        self.code: int | None = None
        self._exit_when = exit_when

    def poll(self) -> int | None:
        if self.code is None and self._exit_when():
            self.code = 0
        return self.code

    def wait(self, timeout_s: float) -> int | None:
        end = time.monotonic() + min(timeout_s, 0.2)
        while time.monotonic() < end:
            if self.poll() is not None:
                return self.code
            time.sleep(0.01)
        return self.poll()

    def kill(self) -> None:
        if self.code is None:
            self.code = 1


class FakeProcesses:
    def __init__(self, env: "EngineEnv") -> None:
        self.env = env
        self.pids = itertools.count(1000, 2)
        self.procs: dict[int, tuple[FakeProc, str]] = {}
        self.spawned: list[list[str]] = []
        self.emulator: FakeProc | None = None
        self.terminated: list[int] = []

    def spawn(self, argv: Sequence[str], *, env: Mapping[str, str], log_path: Path) -> FakeProc:
        argv = list(argv)
        self.spawned.append(argv)
        pid = next(self.pids)
        if "nodaemon" in argv:
            p = FakeProc(pid)
            self.procs[pid] = (p, str(self.env.paths.adb_exe))
            return p
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fake, dev = self.env.fake, self.env.device
        fake.vm_state = ec.VmRunState.RUNNING
        lines = [ACCEL_LINE]
        golden_load_ok = True
        if "-snapshot" in argv:
            golden_load_ok = dev.load_golden() and GOLDEN_SNAPSHOT in fake.snapshots
            if not golden_load_ok:
                lines.append("ERROR | Failed to load snapshot 'orcus_golden'\n")
        else:
            locale = argv[argv.index("-change-locale") + 1] if "-change-locale" in argv else None
            dev.cold_boot(wipe="-wipe-data" in argv, change_locale=locale)
        lines += self.env.extra_log_lines
        log_path.write_text("".join(lines), encoding="utf-8")
        exits_now = not golden_load_ok

        def gone() -> bool:
            if exits_now or fake.vm_state == ec.VmRunState.SHUTDOWN:
                if fake.vm_state == ec.VmRunState.SHUTDOWN:
                    dev.power_off()
                return True
            return False
        p = FakeProc(pid, gone)
        self.procs[pid] = (p, str(self.env.paths.emulator_exe))
        self.emulator = p
        return p

    def sdk_processes(self) -> list[tuple[int, str]]:
        return [(pid, image) for pid, (p, image) in self.procs.items() if p.poll() is None]

    def terminate(self, pid: int) -> bool:
        self.terminated.append(pid)
        if pid in self.procs:
            self.procs[pid][0].kill()
            return True
        if self.emulator is not None and pid == self.emulator.pid + 1:   # the "qemu" of the current emulator
            self.emulator.kill()
            return True
        return False

    def alive(self, pid: int) -> bool:
        if pid in self.procs:
            return self.procs[pid][0].poll() is None
        return self.emulator is not None and pid == self.emulator.pid + 1 and self.emulator.poll() is None

    def adb_server_alive(self) -> bool:
        return any(p.poll() is None and image.endswith("adb.exe") for p, image in self.procs.values())


class FakeDiscovery:
    def __init__(self, env: "EngineEnv") -> None:
        self.env = env
        self.orphans: list[DiscoveryRecord] = []

    def find(self, serial_port: int) -> DiscoveryRecord | None:
        emu = self.env.procs.emulator
        if emu is None or emu.poll() is not None:
            return None
        return DiscoveryRecord(emu.pid + 1, serial_port, serial_port + 1, self.env.fake.port, self.env.fake.token,
                               "orcus36", str(self.env.paths.avd_data), None, self.env.paths.tmp / "pid.ini")

    def ours(self) -> list[DiscoveryRecord]:
        return list(self.orphans)


class FakeAdb:
    def __init__(self, env: "EngineEnv") -> None:
        self.env = env
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str], timeout_s: float) -> CmdResult:
        argv = list(argv)
        self.calls.append(argv)
        self.env.sync_golden_file()

        def res(code: int, out: str = "") -> CmdResult:
            return CmdResult(tuple(argv), code, out, "", 0.0, False)
        if not self.env.procs.adb_server_alive():
            return res(1, "")
        rest = argv[argv.index("-s") + 2:] if "-s" in argv else argv[4:]
        verb = rest[0] if rest else ""
        emu = self.env.procs.emulator
        up = emu is not None and emu.poll() is None and self.env.device.adb_ok
        match verb:
            case "version":
                return res(0, "Android Debug Bridge version 1.0.41")
            case "get-state":
                return res(0, "device") if up else res(1)
            case "shell" if up:
                code, out = self.env.device.shell(rest[1])
                return res(code, out)
            case "install" | "uninstall" if up:
                return res(0, "Success")
            case _:
                return res(1)


class FakePorts:
    def choose(self, preferred: PortSettings) -> Ports:
        return Ports(preferred.console, preferred.console + 1, preferred.grpc, preferred.adb_server)

    def save(self, chosen: Ports) -> None:
        pass


class FakeInput:
    def __init__(self) -> None:
        self.released = 0

    def release_all(self) -> None:
        self.released += 1


class FakeSession:
    def __init__(self, endpoint: DeviceEndpoint) -> None:
        self.endpoint = endpoint
        self.epoch = endpoint.epoch
        self.input = FakeInput()
        self.frames: Any = None
        self.controls: Any = None
        self.closed = False

    def logcat(self):   # type: ignore[no-untyped-def]
        raise NotImplementedError

    def screenshot_png(self, on_done) -> None:   # type: ignore[no-untyped-def]
        on_done(None)

    def close(self) -> None:
        self.closed = True


class EngineEnv:
    """A tmp OrcusPaths with an installed-looking SDK, the fakes, and an EngineManager over them."""

    def __init__(self, tmp_path: Path, *, settings: Settings | None = None) -> None:
        self.paths = OrcusPaths(tmp_path / "home")
        for f in (self.paths.emulator_exe, self.paths.adb_exe, self.paths.avd_ini, Path(f"{self.paths.adbkey}.pub")):
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x", encoding="utf-8")
        (self.paths.avd_data / "config.ini").parent.mkdir(parents=True, exist_ok=True)
        (self.paths.avd_data / "config.ini").write_text("hw.ramSize=2560\n", encoding="utf-8")
        self.paths.state.mkdir(parents=True, exist_ok=True)
        self.paths.logs.mkdir(parents=True, exist_ok=True)
        self.paths.tmp.mkdir(parents=True, exist_ok=True)
        self.settings = settings or Settings()
        self.extra_log_lines: list[str] = []
        self.device = FakeDevice()
        self.fake = HookedFake(self)
        self.fake.start()
        self.procs = FakeProcesses(self)
        self.discovery = FakeDiscovery(self)
        self.adb = FakeAdb(self)
        self.clock = FastClock()
        self.sessions: list[FakeSession] = []
        self.seams = Seams(procs=self.procs, discovery=self.discovery, adb=self.adb, register=lambda a, b: "OKAY",
                           ports=FakePorts(), clock=self.clock, channel=make_channel, whpx_on=lambda: True,
                           disk_free=lambda p: 100 * GIB, env=lambda port: dict(os.environ),
                           home_problems=lambda p: [])
        self.manager = EngineManager(self.paths, lambda: self.settings, self.seams, self._session, owner_kind="test")

    def _session(self, endpoint: DeviceEndpoint) -> Any:
        s = FakeSession(endpoint)
        self.sessions.append(s)
        return s

    def sync_golden_file(self) -> None:
        ram = self.paths.golden_dir / "ram.bin"
        if GOLDEN_SNAPSHOT in self.fake.snapshots:
            ram.parent.mkdir(parents=True, exist_ok=True)
            if not ram.exists():
                ram.write_bytes(b"ram")
        elif ram.exists():
            ram.unlink()

    def modes(self) -> list[str]:
        """Launch modes of the emulator spawns, in order."""
        out = []
        for argv in self.procs.spawned:
            if "nodaemon" in argv:
                continue
            out.append("GOLDEN" if "-snapshot" in argv else "REBUILD" if "-wipe-data" in argv
                       else "PROVISION" if "-change-locale" in argv else "COLD")
        return out

    def close(self) -> None:
        self.manager.close(timeout_s=10)
        self.fake.stop()
