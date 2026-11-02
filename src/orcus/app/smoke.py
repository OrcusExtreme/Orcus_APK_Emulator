"""`orcus-cli smoke` (ARCHITECTURE.md §12.5): the gate for emulator, image and platform-tools upgrades.

start -> frame -> key -> install and launch -> tap -> paste -> shake -> rotate -> location -> reset -> stop,
each step timed. The APK is the user's own HelloPurr build (not shipped); without one the app steps are SKIP.
The tap is checked through HelloPurr's Vibrate(500), which `dumpsys vibrator_manager` records with the package
(S5). HelloPurr's Shaking plays the meow, and App Inventor's Sound logs every Play: "SoundPool.play returned
stream id", or "Unable to play because MinimumInterval has not elapsed" within 500 ms of the tap's meow
(`dumpsys audio` logs no second start of the same player).
A turn shows in the frames' rotation and in the display of an app that follows the sensor; a location shows as
the gps and fused test providers' last location.
"""

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from orcus.apk import launch
from orcus.common import winjob
from orcus.contracts import (
    DeviceControls,
    DeviceSession,
    EngineState,
    Frame,
    HwKey,
    LogEntry,
    OrcusError,
    OrcusFailure,
    PointerAt,
    Rotation,
    RunEvent,
    RunStage,
    Sensitivity,
)

from .session import Session

BOUNDS = re.compile(r'class="([^"]*)" package="([^"]+)"[^>]*?\sclickable="true"[^>]*?'
                    r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"')
PANEL = (720, 1280)
BUSAN = (35.1798, 129.0750)   # anywhere but the session's Seoul City Hall
SOUND_INTERVAL_S = 0.6        # App Inventor Sound.MinimumInterval is 500 ms


@dataclass(frozen=True)
class Step:
    name: str
    status: str      # PASS | FAIL | SKIP
    seconds: float
    detail: str


class _Smoke:
    def __init__(self, session: Session, apk: Path | None, on_step: Callable[[Step], None]) -> None:
        self.s = session
        self.apk = apk
        self.on_step = on_step
        self.steps: list[Step] = []
        self.package: str | None = None
        self.seq = 0

    def step(self, name: str, fn: Callable[[], str | None]) -> bool:
        t = time.monotonic()
        try:
            detail = fn()
            st = Step(name, "SKIP" if detail is None else "PASS", time.monotonic() - t, detail or "")
        except OrcusFailure as e:
            st = Step(name, "FAIL", time.monotonic() - t, f"{e.error.code}: {e.error.detail}")
        except Exception as e:   # a smoke test reports, it does not crash
            st = Step(name, "FAIL", time.monotonic() - t, f"{type(e).__name__}: {e}")
        self.steps.append(st)
        self.on_step(st)
        return st.status != "FAIL"

    # --- steps --------------------------------------------------------------------------------------------------
    def start(self) -> str:
        self.s.engine.start()
        st = self.s.engine.wait_ready(900)
        return f"{st.mode} epoch {st.epoch} ({st.epoch_cause}), GPU {st.caps.gpu_mode if st.caps else '?'}"

    def frame(self) -> str:
        dev = self._device()
        got: list[Frame] = []
        ended: list[OrcusError | None] = []
        first = threading.Event()

        def on_frame(f: Frame) -> None:
            got.append(f)
            first.set()

        def on_end(err: OrcusError | None) -> None:
            ended.append(err)
            first.set()
        dev.frames.start(432, 768, on_frame, on_end)
        try:
            if not first.wait(5) or not got:
                raise AssertionError(f"no frame in 5 s ({ended[0].code if ended and ended[0] else 'no error'})")
        finally:
            dev.frames.stop()
        f = got[0]
        if (f.width, f.height) != (432, 768) or not any(f.rgb888[::997]):
            raise AssertionError(f"frame {f.width}x{f.height}, blank={not any(f.rgb888[::997])}")
        self.seq = f.seq
        return f"{f.width}x{f.height} {f.rotation.name}"

    def key(self) -> str:
        self._device().input.key(HwKey.HOME)
        focus = ""
        end = time.monotonic() + 5
        while time.monotonic() < end:
            out = self.s.engine.shell().shell(["dumpsys", "window"], timeout_s=10).out
            focus = next((x.strip() for x in out.splitlines() if "mCurrentFocus=" in x), "")
            if "launcher" in focus.lower():
                return focus
            time.sleep(0.3)
        raise AssertionError(f"Home did not reach the launcher: {focus!r}")

    def install(self) -> str | None:
        if self.apk is None:
            return None
        events: list[RunEvent] = []
        done = threading.Event()

        def on_event(ev: RunEvent) -> None:
            events.append(ev)
            if ev.stage in (RunStage.RUNNING, RunStage.FAILED, RunStage.CANCELLED, RunStage.CRASHED,
                            RunStage.EXITED):
                done.set()
        unsubscribe = self.s.apps.subscribe(on_event)
        try:
            run_id = self.s.apps.submit(self.apk)
            if not done.wait(120):
                raise AssertionError("no result in 120 s")
        finally:
            unsubscribe()
        last = next(e for e in reversed(events) if e.run_id == run_id)
        if last.stage is not RunStage.RUNNING:
            raise AssertionError(f"{last.stage}: {last.problem.code if last.problem else ''}")
        self.package = last.info.package if last.info else None
        return f"{last.elapsed_s:.1f} s, pid {last.pid}"

    def tap(self) -> str | None:
        if self.package is None:
            return None
        sh = self.s.engine.shell()
        sh.shell(["uiautomator", "dump", "/sdcard/orcus_smoke.xml"], timeout_s=20)
        xml = sh.shell(["cat", "/sdcard/orcus_smoke.xml"], timeout_s=10).out
        # the biggest Button of the app (HelloPurr's kitty), not the action bar's overflow menu
        views = sorted(((cls.endswith("Button"), (int(c) - int(a)) * (int(d) - int(b)),
                         ((int(a) + int(c)) / 2, (int(b) + int(d)) / 2))
                        for cls, p, a, b, c, d in BOUNDS.findall(xml) if p == self.package), reverse=True)
        if not views:
            raise AssertionError("no clickable view of the app in the dump")
        target = views[0][2]
        at = PointerAt(target[0] / PANEL[0], target[1] / PANEL[1], self.seq)
        inp = self._device().input
        inp.pointer(at, True)
        time.sleep(0.08)
        inp.pointer(at, False)
        if not self.package.endswith(".HelloPurr"):
            return f"tapped {target} (not HelloPurr: not checked)"
        end = time.monotonic() + 4
        while time.monotonic() < end:
            out = sh.shell(["dumpsys", "vibrator_manager"], timeout_s=10).out
            if self.package in out:
                return f"tapped {target}, vibration recorded"
            time.sleep(0.3)
        raise AssertionError(f"tapped {target} but no vibration from {self.package}")

    def paste(self) -> str:
        self._device().input.paste_text("안녕하세요 Orcus")
        return "sent (not read back)"

    def shake(self) -> str | None:
        if self.package is None:
            return None
        played = threading.Event()

        def on_log(e: LogEntry) -> None:
            if e.tag == "Sound" and ("SoundPool.play returned" in e.msg or "MinimumInterval" in e.msg):
                played.set()   # Play() ran: only Shaking calls it now
        unsubscribe = self.s.apps.subscribe_log(on_log)
        try:
            time.sleep(SOUND_INTERVAL_S)   # the tap's meow: Sound refuses a second Play within 500 ms
            self._controls().shake(Sensitivity.MODERATE)   # HelloPurr keeps the default sensitivity
            if not self.package.endswith(".HelloPurr"):
                return "sent (not HelloPurr: not checked)"
            if not played.wait(5):
                raise AssertionError("no Shaking: HelloPurr's Sound.Play did not run")
        finally:
            unsubscribe()
        return "Shaking played HelloPurr's sound"

    def rotate(self) -> str:
        dev, controls = self._device(), self._controls()
        got: list[Frame] = []
        dev.frames.start(360, 640, got.append, lambda err: None)
        try:
            took = self._turn(controls, got, Rotation.LANDSCAPE)
            display = "not checked (no app)"
            if self.package is not None:   # the launcher stays at 0°; HelloPurr follows the sensor
                sh = self.s.engine.shell()
                end = time.monotonic() + 4
                while (shown := launch.display_rotation(sh)) != Rotation.LANDSCAPE:
                    if time.monotonic() > end:
                        raise AssertionError(f"the frames turned but the app's display stayed {shown}")
                    time.sleep(0.3)
                display = "turned too"
            self._turn(controls, got, Rotation.PORTRAIT)
        finally:
            dev.frames.stop()
        return f"frames LANDSCAPE in {took:.1f} s (15° steps), display {display}, back to PORTRAIT"

    def location(self) -> str:
        t = time.monotonic()
        self._controls().set_location(*BUSAN)
        want = f"{BUSAN[0]:.6f},{BUSAN[1]:.6f}"
        end = t + 10
        while time.monotonic() < end:
            out = self.s.engine.shell().shell(["dumpsys", "location"], timeout_s=20).out
            if f"Location[gps {want}" in out and f"Location[fused {want}" in out:
                return f"gps and fused test providers at {want} in {time.monotonic() - t:.1f} s"
            time.sleep(0.5)
        raise AssertionError(f"the test providers never reported {want}")

    def reset(self) -> str:
        t = time.monotonic()
        target = self.s.engine.reset_device()
        st = self.s.engine.wait_ready(180, min_epoch=target)
        if self.package is not None:
            listed = self.s.engine.shell().shell(["pm", "list", "packages", self.package], timeout_s=15).out
            if f"package:{self.package}" in listed:
                raise AssertionError("the app survived the reset")
        return f"{time.monotonic() - t:.1f} s, epoch {st.epoch} ({st.epoch_cause})"

    def stop(self, sdk: Path) -> str:
        self.s.engine.stop()
        end = time.monotonic() + 30
        while self.s.engine.status().state != EngineState.STOPPED and time.monotonic() < end:
            time.sleep(0.1)
        if self.s.engine.status().state != EngineState.STOPPED:
            raise AssertionError(f"still {self.s.engine.status().state} after 30 s")
        time.sleep(1.0)
        left = [image for _pid, image in winjob.processes_under(sdk) if not image.lower().endswith("adb.exe")]
        if left:
            raise AssertionError(f"left behind: {left}")
        return "no SDK processes left (adb server excepted)"

    def _device(self) -> DeviceSession:
        dev = self.s.device()
        if dev is None:
            raise AssertionError("no device session")
        return dev

    def _controls(self) -> DeviceControls:
        controls = self._device().controls
        if controls is None:
            raise AssertionError("no device controls")
        return controls

    @staticmethod
    def _turn(controls: DeviceControls, got: list[Frame], to: Rotation) -> float:
        t = time.monotonic()
        controls.rotate(to)
        while not got or got[-1].rotation != to:
            if time.monotonic() - t > 5:
                raise AssertionError(f"no {to.name} frame 5 s after the turn")
            time.sleep(0.05)
        return time.monotonic() - t


def run(session: Session, sdk: Path, apk: Path | None, on_step: Callable[[Step], None]) -> list[Step]:
    sm = _Smoke(session, apk, on_step)
    if sm.step("start", sm.start):
        sm.step("frame", sm.frame)
        sm.step("key", sm.key)
        sm.step("install", sm.install)
        sm.step("tap", sm.tap)
        sm.step("paste", sm.paste)
        sm.step("shake", sm.shake)
        sm.step("rotate", sm.rotate)
        sm.step("location", sm.location)
        sm.step("reset", sm.reset)
    sm.step("stop", lambda: sm.stop(sdk))
    return sm.steps


def table(steps: list[Step]) -> str:
    rows = [f"{s.status:<4} {s.name:<8} {s.seconds:6.1f} s  {s.detail}" for s in steps]
    verdict = "FAIL" if any(s.status == "FAIL" for s in steps) else "PASS"
    return "\n".join([*rows, f"=> {verdict}"])
