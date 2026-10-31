"""A fake engine and phone for ApkService tests: epoch-bound shells, a few am/pm commands, scripted logcat.

Test APKs are tiny zips whose AndroidManifest.xml holds JSON; `fake_inspect` turns that into ApkInfo and the
phone reads the package from it when installing.
"""

import json
import queue
import threading
import time
import zipfile
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import CancelledError
from dataclasses import replace
from pathlib import Path

from orcus.common import messages_ko
from orcus.contracts import (
    ApkInfo,
    CmdResult,
    DeviceCaps,
    EngineNotReady,
    EngineState,
    EngineStatus,
    EpochCause,
    ErrorCode,
    GpuMode,
    LogEntry,
    OrcusError,
    OrcusFailure,
    Rotation,
    RunEvent,
    SessionMode,
)

CAPS = DeviceCaps(api=36, abis=("x86_64", "arm64-v8a"), panel=(720, 1280, 320), renderer="ANGLE",
                  gpu_mode=GpuMode.HOST, hypervisor="WHPX", locale="ko-KR", timezone="Asia/Seoul",
                  tts_engine="com.google.android.tts", tts_ko=True, speech_recognizer=True, imes=())
SYSTEM_PID = 512


def make_apk(directory: Path, name: str, package: str, **manifest: object) -> Path:
    path = directory / f"{name}.apk"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("AndroidManifest.xml", json.dumps({"package": package, **manifest}))
        z.writestr("classes.dex", name)
    return path


def _manifest(apk: Path) -> dict[str, object]:
    with zipfile.ZipFile(apk) as z:
        return json.loads(z.read("AndroidManifest.xml"))


def fake_inspect(apk: Path, sha256: str) -> ApkInfo:
    m = _manifest(apk)
    if m.get("unreadable"):
        raise ValueError("bad AXML")
    package = str(m["package"])
    return ApkInfo(sha256=sha256, staged=apk, package=package, launcher=f"{package}.Screen1",
                   label=package.rsplit(".", 1)[-1], launcher_orientation=str(m.get("orientation", "unspecified")),
                   version_code=1, version_name="1.0", min_sdk=int(str(m.get("min_sdk", 14))), target_sdk=36,
                   permissions=frozenset(str(p) for p in m.get("permissions", [])),  # type: ignore[union-attr]
                   queries=frozenset(), features=frozenset(), native_abis=frozenset(), asset_elf=False,
                   is_app_inventor=True, is_companion=False, ai_era="nb206+", icon_png=None)


def now_us() -> int:
    return int(time.time() * 1_000_000)


class FakeLogcat:
    def __init__(self) -> None:
        self._q: "queue.Queue[LogEntry | None]" = queue.Queue()
        self.cancelled = False

    def push(self, e: LogEntry) -> None:
        self._q.put(e)

    def cancel(self) -> None:
        self.cancelled = True
        self._q.put(None)

    def __iter__(self) -> Iterator[LogEntry]:
        while True:
            e = self._q.get()
            if e is None:
                return
            yield e


class FakeControls:
    def __init__(self) -> None:
        self.rotations: list[Rotation] = []
        self.gradual: list[bool] = []
        self.mics: list[bool] = []
        self.resent = 0
        self.posture = Rotation.PORTRAIT
        self.microphone = False

    def rotate(self, to: Rotation, *, gradual: bool = True) -> None:
        self.rotations.append(to)
        self.gradual.append(gradual)
        self.posture = to

    def shake(self, sensitivity: object) -> None: ...
    def tilt(self, pitch_deg: float, roll_deg: float) -> None: ...
    def set_location(self, lat: float, lon: float, alt_m: float = 30.0) -> None: ...

    def resend_location(self) -> None:
        self.resent += 1

    def set_microphone(self, enabled: bool) -> None:
        self.mics.append(enabled)
        self.microphone = enabled


class FakeSession:
    def __init__(self, epoch: int) -> None:
        self.epoch = epoch
        self.controls = FakeControls()
        self.streams: list[FakeLogcat] = []
        self.closed = False

    def logcat(self) -> FakeLogcat:
        s = FakeLogcat()
        s.push(LogEntry(now_us(), SYSTEM_PID, SYSTEM_PID, "I", "ActivityManager", "stream open"))
        self.streams.append(s)
        return s

    def push(self, e: LogEntry) -> None:
        for s in self.streams:
            if not s.cancelled:
                s.push(e)

    def close(self) -> None:
        self.closed = True
        for s in self.streams:
            s.cancel()


class Phone:
    """What the guest has: installed packages, running processes, and a log of shell commands."""

    def __init__(self) -> None:
        self.packages: dict[str, int] = {}     # package -> install generation (changes the /data/app path)
        self.running: dict[str, int] = {}
        self.next_pid = 4000
        self.commands: list[tuple[str, ...]] = []
        self.installs: list[tuple[str, list[str]]] = []
        self.uninstalls: list[str] = []
        self.install_outputs: list[str] = []   # failures to return before succeeding
        self.on_install: Callable[[], None] | None = None
        self.on_start: Callable[[str, int], None] | None = None
        self.crash_text = ""
        self.no_process = False
        self.display = "ROTATION_0"   # `dumpsys window displays`: a locked or turned app screen

    def wipe(self) -> None:
        self.packages.clear()
        self.running.clear()

    def install(self, apk: Path, flags: Sequence[str]) -> tuple[int, str]:
        pkg = str(_manifest(apk)["package"])
        self.installs.append((pkg, list(flags)))
        hook, self.on_install = self.on_install, None
        if hook is not None:
            hook()
        if self.install_outputs:
            return 1, self.install_outputs.pop(0)
        self.packages[pkg] = self.packages.get(pkg, 0) + 1
        self.running.pop(pkg, None)
        return 0, "Performing Streamed Install\nSuccess"

    def uninstall(self, pkg: str) -> None:
        self.uninstalls.append(pkg)
        self.packages.pop(pkg, None)
        self.running.pop(pkg, None)

    def shell(self, a: tuple[str, ...]) -> tuple[int, str]:
        self.commands.append(a)
        match a:
            case ("am", "force-stop", pkg):
                self.running.pop(pkg, None)
            case ("am", "start", "-W", "-n", comp):
                pkg = comp.split("/")[0]
                if pkg not in self.packages:
                    return 0, "Error type 3\nError: Activity class does not exist."
                if not self.no_process:
                    self.next_pid += 1
                    self.running[pkg] = self.next_pid
                    if self.on_start is not None:
                        self.on_start(pkg, self.next_pid)
                return 0, f"Starting: Intent {{ cmp={comp} }}\nStatus: ok\nTotalTime: 120\nComplete"
            case ("pidof", pkg):
                pid = self.running.get(pkg)
                return (0, f"{pid}\n") if pid else (1, "")
            case ("pm", "list", "packages", "-3", "-f"):
                return 0, "".join(f"package:/data/app/~~{n}/{p}-{n}/base.apk={p}\n" for p, n in self.packages.items())
            case ("pm", "clear", _pkg):
                return 0, "Success"
            case ("pm", "grant", _pkg, perm):
                return (0, "") if "LOCATION" in perm else (255, "Permission is not a changeable permission type")
            case ("cmd", "package", "resolve-activity", *rest):
                pkg = rest[-1]
                return 0, f"priority=0\n{pkg}/.Screen1\n" if pkg in self.packages else "No activity found\n"
            case ("logcat", "-d", "-b", "crash", *_rest):
                return 0, self.crash_text
            case ("dumpsys", "window", "displays"):
                return 0, f"  mCurrentRotation={self.display}\n"
            case _:
                pass
        return 0, ""


def _not_ready(detail: str) -> EngineNotReady:
    return EngineNotReady(messages_ko.error(ErrorCode.ENGINE_UNAVAILABLE, detail))


class FakeShell:
    serial = "emulator-5640"

    def __init__(self, engine: "FakeEngine", epoch: int) -> None:
        self._engine = engine
        self.epoch = epoch

    def _guard(self) -> None:
        st = self._engine.st
        if st.state != EngineState.READY or st.epoch != self.epoch:
            raise _not_ready(f"epoch {self.epoch} is gone")

    def get_state(self) -> str:
        self._guard()
        return "device"

    def shell(self, argv: Sequence[str], timeout_s: float = 30) -> CmdResult:
        self._guard()
        code, out = self._engine.phone.shell(tuple(argv))
        return CmdResult(tuple(argv), code, out, "", 0.01, False)

    def install(self, apk: Path, flags: Sequence[str], timeout_s: float = 180) -> CmdResult:
        self._guard()
        code, out = self._engine.phone.install(apk, flags)
        self._guard()   # the phone restarted while installing
        return CmdResult(("install",), code, out, "", 0.5, False)

    def uninstall(self, package: str) -> CmdResult:
        self._guard()
        self._engine.phone.uninstall(package)
        return CmdResult(("uninstall", package), 0, "Success", "", 0.1, False)


class FakeEngine:
    """EngineControl for ApkService: READY at epoch 1 unless told otherwise."""

    def __init__(self, *, ready: bool = True, mode: SessionMode = SessionMode.GOLDEN) -> None:
        self.cond = threading.Condition()
        self.phone = Phone()
        self.subs: list[Callable[[EngineStatus], None]] = []
        self.sessions: dict[int, FakeSession] = {}
        self.resets = 0
        self.starts = 0
        epoch = 1 if ready else 0
        self.st = EngineStatus(EngineState.READY if ready else EngineState.BOOTING, "engine.state.ready", None,
                               mode, epoch, EpochCause.BOOT, True, (), None, CAPS)
        if ready:
            self.sessions[1] = FakeSession(1)

    # EngineView / EngineControl
    def status(self) -> EngineStatus:
        return self.st

    def start(self) -> None:
        self.starts += 1

    def stop(self, *, fast: bool = False) -> None: ...
    def rebuild(self, reason: str) -> None: ...
    def retry(self) -> None: ...

    def reset_device(self) -> int:
        self.resets += 1
        target = self.st.epoch + 1
        threading.Timer(0.05, self.new_epoch, args=(EpochCause.RESET,)).start()
        return target

    def subscribe(self, cb: Callable[[EngineStatus], None]) -> Callable[[], None]:
        self.subs.append(cb)
        return lambda: self.subs.remove(cb) if cb in self.subs else None

    def wait_ready(self, timeout_s: float, *, min_epoch: int | None = None,
                   cancel: threading.Event | None = None) -> EngineStatus:
        end = time.monotonic() + timeout_s
        with self.cond:
            while True:
                st = self.st
                if st.state == EngineState.READY and (min_epoch is None or st.epoch >= min_epoch):
                    return st
                if st.state == EngineState.FAILED:
                    raise OrcusFailure(st.error or messages_ko.error(ErrorCode.ENGINE_EXIT))
                if cancel is not None and cancel.is_set():
                    raise CancelledError()
                if time.monotonic() > end:
                    raise OrcusFailure(messages_ko.error(ErrorCode.ENGINE_BOOT_TIMEOUT))
                self.cond.wait(0.02)

    def shell(self) -> FakeShell:
        if self.st.state != EngineState.READY:
            raise _not_ready(f"engine is {self.st.state}")
        return FakeShell(self, self.st.epoch)

    def session(self) -> FakeSession:
        if self.st.state != EngineState.READY:
            raise _not_ready(f"engine is {self.st.state}")
        return self.sessions[self.st.epoch]

    # test controls
    def _publish(self, st: EngineStatus) -> None:
        with self.cond:
            self.st = st
            self.cond.notify_all()
        for cb in list(self.subs):
            cb(st)

    def new_epoch(self, cause: EpochCause) -> None:
        """Reset, crash restart, rebuild or a new boot: the phone is the golden one again."""
        old = self.sessions.get(self.st.epoch)
        if old is not None:
            old.close()
        self.phone.wipe()
        epoch = self.st.epoch + 1
        self.sessions[epoch] = FakeSession(epoch)
        self._publish(replace(self.st, state=EngineState.READY, epoch=epoch, epoch_cause=cause))

    def fail(self, error: OrcusError) -> None:
        self._publish(replace(self.st, state=EngineState.FAILED, error=error))

    def current(self) -> FakeSession:
        return self.sessions[self.st.epoch]


class Events:
    def __init__(self) -> None:
        self.items: list[RunEvent] = []
        self._cond = threading.Condition()

    def __call__(self, ev: RunEvent) -> None:
        with self._cond:
            self.items.append(ev)
            self._cond.notify_all()

    def mark(self) -> int:
        with self._cond:
            return len(self.items)

    def since(self, after: int) -> list[RunEvent]:
        with self._cond:
            return self.items[after:]

    def wait(self, pred: Callable[[RunEvent], bool], timeout_s: float = 5.0, *, after: int = 0) -> RunEvent:
        """The first event since index `after` (see mark()) that matches."""
        end = time.monotonic() + timeout_s
        with self._cond:
            while True:
                for ev in self.items[after:]:
                    if pred(ev):
                        return ev
                left = end - time.monotonic()
                if left <= 0:
                    seen = [(e.run_id[:4], e.stage.name, e.problem.code.name if e.problem else None)
                            for e in self.items]
                    raise AssertionError(f"no such event; saw {seen}")
                self._cond.wait(left)

    def stages(self, run_id: str) -> list[str]:
        with self._cond:
            return [e.stage.name for e in self.items if e.run_id == run_id]
