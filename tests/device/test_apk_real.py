"""Device tests for the APK pipeline (ARCHITECTURE.md §12.3 items 4, 8, 13) on the real phone.

Run: py tools/dev.py test -m device tests/device/test_apk_real.py -s
Needs the fixtures in D:\\OrcusDev\\fixtures (HelloPurr.apk, OrcusProbe.apk, built in the App Inventor web IDE).
Taps go through our own input path (gRPC MouseEvent with the §9.2 table); buttons are found with uiautomator.
"""

import re
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from orcus.app import compose
from orcus.app.session import Session
from orcus.contracts import EpochCause, ErrorCode, PointerAt, RemedyAction, RunEvent, RunPolicy, RunStage

FIX = Path(r"D:\OrcusDev\fixtures")
HELLO, PROBE = FIX / "HelloPurr.apk", FIX / "OrcusProbe.apk"
TTS_PKG, ASI_PKG = "com.google.android.tts", "com.google.android.as"
W, H = 720, 1280

pytestmark = [pytest.mark.device,
              pytest.mark.skipif(not PROBE.exists(), reason="fixtures live in D:\\OrcusDev\\fixtures")]


@dataclass(frozen=True)
class Node:
    text: str
    rid: str
    cls: str
    pkg: str
    bounds: tuple[int, int, int, int]

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2


class Phone:
    """The real Session plus what a test needs: run events, uiautomator dumps and taps."""

    def __init__(self, s: Session) -> None:
        self.s = s
        self.events: list[RunEvent] = []
        self._cond = threading.Condition()
        self.log: deque[str] = deque(maxlen=20000)   # every guest line, for failure evidence
        s.apps.subscribe(self._on_event)
        s.apps.subscribe_log(lambda e: self.log.append(f"{e.pid} {e.level} {e.tag}: {e.msg}"), app_only=False)

    def _on_event(self, ev: RunEvent) -> None:
        with self._cond:
            self.events.append(ev)
            self._cond.notify_all()

    def mark(self) -> int:
        with self._cond:
            return len(self.events)

    def wait(self, pred: Callable[[RunEvent], bool], after: int, timeout_s: float) -> RunEvent:
        end = time.monotonic() + timeout_s
        with self._cond:
            while True:
                for ev in self.events[after:]:
                    if pred(ev):
                        return ev
                left = end - time.monotonic()
                if left <= 0:
                    seen = [(e.stage.name, e.problem.code.name if e.problem else None, e.problem.message_key
                             if e.problem else None) for e in self.events[after:]]
                    raise AssertionError(f"timed out; events since mark: {seen}\n{self.evidence()}")
                self._cond.wait(left)

    def evidence(self) -> str:
        """For a failure message: what the screen shows, a screenshot on D:, and the app's recent warnings."""
        try:
            texts = [n.text for n in self.dump() if n.text][:30]
        except AssertionError as e:
            texts = [f"dump failed: {e}"]
        shot = Path(r"D:\OrcusDev\pytest\device-failure.png")
        self.sh("screencap", "-p", "/sdcard/orcus_fail.png")
        png = self.s.engine.shell().shell(["base64", "/sdcard/orcus_fail.png"], timeout_s=30).out
        try:
            import base64
            shot.write_bytes(base64.b64decode(png))
        except ValueError:
            pass
        warn = [x for x in self.s.report().splitlines() if re.search(r" [WEF] ", x)][-15:]
        speech = [x for x in list(self.log) if re.search(r"Recogni|Speech|ErrorOccurred", x)][-12:]
        return (f"--- screen ---\n{texts}\n--- screenshot: {shot} ---\n--- warnings ---\n" + "\n".join(warn)
                + "\n--- speech / errors ---\n" + "\n".join(x[:200] for x in speech))

    def stages(self, run_id: str, after: int) -> list[str]:
        with self._cond:
            return [e.stage.name for e in self.events[after:] if e.run_id == run_id]

    def run(self, apk: Path, policy: RunPolicy | None = None, timeout_s: float = 90) -> tuple[RunEvent, int]:
        m = self.mark()
        rid = self.s.apps.submit(apk, policy)
        ev = self.wait(lambda e: e.run_id == rid and e.stage in (RunStage.RUNNING, RunStage.FAILED), m, timeout_s)
        assert ev.stage is RunStage.RUNNING, ev.problem
        return ev, m

    def rerun(self) -> RunEvent:
        m = self.mark()
        self.s.apps.rerun()
        return self.wait(lambda e: e.stage is RunStage.RUNNING, m, 60)

    def sh(self, *argv: str, timeout_s: float = 30) -> str:
        return self.s.engine.shell().shell(list(argv), timeout_s=timeout_s).out

    # --- screen ---------------------------------------------------------------------------------------------------
    def dump(self, quiet_pkg: str | None = None) -> list[Node]:
        quieted = False
        try:
            for _ in range(3):
                if "dumped to" in self.sh("uiautomator", "dump", "/sdcard/orcus_t.xml", timeout_s=45):
                    xml = self.sh("cat", "/sdcard/orcus_t.xml")
                    root = ET.fromstring(xml[xml.index("<"):])
                    out = []
                    for e in root.iter("node"):
                        b = [int(v) for v in re.findall(r"-?\d+", e.get("bounds", "[0,0][0,0]"))]
                        out.append(Node(e.get("text", ""), e.get("resource-id", ""), e.get("class", ""),
                                        e.get("package", ""), (b[0], b[1], b[2], b[3])))
                    return out
                if quiet_pkg and not quieted:   # sensor labels keep the UI busy: idle the app's sensors (S7)
                    self.sh("cmd", "sensorservice", "set-uid-state", quiet_pkg, "idle")
                    quieted = True
            raise AssertionError("uiautomator dump failed")
        finally:
            if quieted and quiet_pkg:
                self.sh("cmd", "sensorservice", "set-uid-state", quiet_pkg, "reset")

    def tap(self, x: int, y: int) -> None:
        dev = self.s.device()
        assert dev is not None
        at = PointerAt(x / W, y / H, 0)
        dev.input.pointer(at, True)
        time.sleep(0.06)
        dev.input.pointer(at, False)
        time.sleep(0.3)

    def _visible(self, text: str, pkg: str) -> Node | None:
        n = next((n for n in self.dump(pkg) if n.text == text), None)
        # dumps report the visible part: a button mostly under the nav bar (from y 1184) shows as a 13 px sliver;
        # the last row, scrolled to the end, ends right at 1184
        ok = n is not None and n.bounds[1] >= 160 and n.bounds[3] <= 1184 and n.bounds[3] - n.bounds[1] >= 50
        return n if ok else None

    def scroll_to(self, text: str, pkg: str) -> Node:
        swiped = False
        for up in (True, False):
            for _ in range(7):
                n = self._visible(text, pkg)
                if n is not None and swiped:   # a fling may still be moving the list: the spot must hold still
                    time.sleep(0.6)
                    again = self._visible(text, pkg)
                    n = again if again is not None and again.bounds == n.bounds else None
                if n is not None:
                    return n
                y0, y1 = ("1100", "560") if up else ("560", "1100")
                self.sh("input", "swipe", "650", y0, "650", y1, "350")
                swiped = True
                time.sleep(0.8)
        raise AssertionError(f"button not found: {text}")

    def answer_permission(self, allow: bool, pkg: str) -> str | None:
        ids = (("permission_allow_foreground_only_button", "permission_allow_button") if allow
               else ("permission_deny_button", "permission_deny_and_dont_ask_again_button"))
        nodes = self.dump(pkg)
        for suffix in ids:
            n = next((n for n in nodes if n.rid.endswith(":id/" + suffix)), None)
            if n is not None:
                self.tap(*n.center)
                time.sleep(1.0)
                return n.text
        return None

    def press(self, text: str, pkg: str, want: Callable[[RunEvent], bool], timeout_s: float = 15,
              before_wait: Callable[[], None] | None = None) -> RunEvent:
        n = self.scroll_to(text, pkg)
        m = self.mark()
        self.tap(*n.center)
        if before_wait is not None:
            before_wait()
        return self.wait(lambda e: e.problem is not None and want(e), m, timeout_s)

    def dismiss_dialog(self, pkg: str) -> None:
        buttons = [n for n in self.dump(pkg) if n.cls == "android.widget.Button" and n.rid.startswith("android:id/b")]
        if buttons:
            self.tap(*buttons[0].center)
        else:
            dev = self.s.device()
            assert dev is not None
            dev.input.key("GoBack")
        time.sleep(1.0)


def key_is(key: str) -> Callable[[RunEvent], bool]:
    return lambda e: e.problem is not None and e.problem.message_key == key


def code_is(code: ErrorCode) -> Callable[[RunEvent], bool]:
    return lambda e: e.problem is not None and e.problem.code is code


@pytest.fixture(scope="module")
def phone() -> Iterator[Phone]:
    compose.ensure_job()
    paths = compose.make_paths()
    print(f"\nlog: {compose.start_logging(paths, 'devtest')}")
    s = Session(paths, compose.load_settings(paths), owner_kind="test")
    ph = Phone(s)
    s.engine.start()
    st = s.engine.wait_ready(900)
    print(f"\nphone READY: {st.mode} epoch {st.epoch} ({st.epoch_cause})")
    yield ph
    s.shutdown()


@pytest.mark.timeout(600)
def test_install_fast_path_and_reset(phone: Phone) -> None:
    """§12.3 item 4 (fast path, reinstall after reset) and 8 (reset epoch); B1 sample with the engine warm."""
    t = time.monotonic()
    first, m = phone.run(HELLO, RunPolicy.FRESH_APP)
    first_s = time.monotonic() - t
    assert "INSTALLING" in phone.stages(first.run_id, m) and first.info is not None
    pkg = first.info.package
    again, m = phone.run(HELLO)
    assert "INSTALLING" not in phone.stages(again.run_id, m)          # same file, same epoch
    epoch = phone.s.engine.status().epoch
    target = phone.s.engine.reset_device()
    st = phone.s.engine.wait_ready(180, min_epoch=target)
    assert (st.epoch, st.epoch_cause) == (epoch + 1, EpochCause.RESET)
    phone.wait(lambda e: e.run_id == again.run_id and e.stage is RunStage.EXITED, m, 30)
    assert f"package:{pkg}" not in phone.sh("pm", "list", "packages", pkg)
    third, m = phone.run(HELLO)
    assert "INSTALLING" in phone.stages(third.run_id, m)             # the fast path is per epoch
    print(f"drop -> running: install {first_s:.1f} s, fast path {again.elapsed_s:.1f} s, "
          f"after reset {third.elapsed_s:.1f} s")
    assert first_s <= 10 and again.elapsed_s <= 10                     # B1 with the engine warm
    m = phone.mark()
    phone.sh("am", "crash", pkg)                                        # a Java crash: one card, not also 909
    crash = phone.wait(lambda e: e.run_id == third.run_id and e.stage is RunStage.CRASHED, m, 20)
    assert crash.problem is not None and crash.problem.code is ErrorCode.APP_CRASH
    assert "CrashedByAdbException" in crash.problem.detail
    time.sleep(2)
    assert not [e for e in phone.events[m:] if e.problem is not None and e.problem.code is not ErrorCode.APP_CRASH]


@pytest.mark.timeout(600)
def test_probe_error_cards(phone: Phone) -> None:
    """§12.3 item 13: each App Inventor error path gives its card (S5 strings)."""
    ev, _ = phone.run(PROBE, RunPolicy.FRESH_APP)
    assert ev.info is not None
    pkg = ev.info.package
    time.sleep(2)
    phone.answer_permission(True, pkg)           # LocationSensor asks at start (no -g)

    def deny() -> None:
        time.sleep(1.5)
        phone.answer_permission(False, pkg)

    phone.press("scan", pkg, key_is("app.error.1501"))
    phone.press("bad intent", pkg, key_is("app.error.601"))
    denied = phone.press("record", pkg, key_is("app.error.908"), before_wait=deny)
    assert denied.problem is not None and denied.problem.remedy is not None
    assert denied.problem.remedy.action is RemedyAction.GRANT_ALL
    # granted the way [Allow all permissions] does it; a dialog grant needs a second press (S5)
    phone.sh("pm", "grant", pkg, "android.permission.RECORD_AUDIO")
    # mic off -> No Match: 5.5 s in S5, 6 s alone, 26 s after the 908 step, and sometimes never: Google's
    # recognizer then ends with "#onResults empty final recognition results" and no NO_SPEECH error, so the app
    # reports nothing. Our part is checked whenever the app does report it.
    try:
        phone.press("listen (new)", pkg, key_is("app.error.3806"), timeout_s=60)
    except AssertionError:
        if any("errorNumber = 3806" in x for x in list(phone.log)):
            raise   # the app reported it and the card is missing: ours
        print("3806 not checked this run: the recognizer did not finish with NO_SPEECH")

    phone.press("block error", pkg, code_is(ErrorCode.APP_AI_RUNTIME_ERROR))
    phone.dismiss_dialog(pkg)                    # END APPLICATION finishes Screen1; the process lives on
    phone.rerun()

    def poke() -> None:                          # input while the loop blocks the main thread
        time.sleep(1.0)
        for _ in range(4):
            phone.tap(360, 300)
            time.sleep(0.7)
    anr = phone.press("ANR loop", pkg, code_is(ErrorCode.APP_ANR), timeout_s=40, before_wait=poke)
    assert anr.stage is RunStage.RUNNING
    phone.rerun()                                # force-stop ends the hung app without an APP_DIED card

    m = phone.mark()
    phone.sh("am", "crash", pkg)   # hits the WebViewer's renderer first: the app then dies natively (SIGTRAP)
    crash = phone.wait(lambda e: e.stage is RunStage.CRASHED, m, 20)
    assert crash.problem is not None and crash.problem.code is ErrorCode.APP_CRASH
    assert crash.problem.detail.startswith("Fatal signal")


@pytest.mark.timeout(900)
def test_missing_speech_and_tts(phone: Phone) -> None:
    """§12.3 item 13 with the TTS and speech providers disabled; [Reset phone] brings them back."""
    ev, _ = phone.run(PROBE, RunPolicy.FRESH_APP)
    assert ev.info is not None
    pkg = ev.info.package
    time.sleep(2)
    phone.answer_permission(True, pkg)
    phone.sh("pm", "grant", pkg, "android.permission.RECORD_AUDIO")
    try:
        for p in (TTS_PKG, ASI_PKG):
            phone.sh("pm", "disable-user", "--user", "0", p)
        phone.rerun()
        legacy = phone.press("listen (legacy)", pkg, code_is(ErrorCode.APP_SPEECH_MISSING))
        assert legacy.problem is not None and legacy.problem.message_key == "app.speech_missing"
        if legacy.stage is RunStage.RUNNING:
            phone.dismiss_dialog(pkg)
        phone.rerun()
        phone.press("listen (new)", pkg, code_is(ErrorCode.APP_SPEECH_MISSING))   # bind error 10
        phone.rerun()
        phone.press("voices", pkg, key_is("app.error.2701"), timeout_s=30)        # ~12 s (S5)
        phone.press("speak", pkg, code_is(ErrorCode.APP_TTS_MISSING), timeout_s=40)
    finally:
        target = phone.s.engine.reset_device()   # the golden has both providers enabled
        phone.s.engine.wait_ready(180, min_epoch=target)
    assert "package:" + TTS_PKG in phone.sh("pm", "list", "packages", "-e", TTS_PKG)
