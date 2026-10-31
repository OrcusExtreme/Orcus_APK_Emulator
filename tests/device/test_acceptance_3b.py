"""Phase 3b acceptance on the real phone (plan.md §3: B3, B5, B6, B13), plus the wheel and the turn method.
B14 and the window parts of B5/B13 (the window turning, location hints, settings, the close notice) are unit tests
(tests/unit/test_gui_3b.py); B10's manual checks are the user's.

Run: py tools/dev.py test -m device tests/device/test_acceptance_3b.py -s
Needs D:\\OrcusDev\\fixtures: OrcusProbe, TalkToMe (built in the App Inventor web IDE). Every test starts OrcusProbe
in portrait with its permissions granted. Lines starting with RESULT are the measurements for plan.md §4.
"""

import dataclasses
import re
import threading
import time
from collections.abc import Iterator

import pytest
from google.protobuf import empty_pb2

from orcus.app import compose
from orcus.app.session import Session
from orcus.contracts import (
    DeviceControls,
    ErrorCode,
    Frame,
    PointerAt,
    PrivacySettings,
    Rotation,
    RunStage,
    Sensitivity,
)
from test_acceptance_3a import label, result, until
from test_apk_real import FIX, PROBE, H, Node, Phone, W

TALK = FIX / "TalkToMe.apk"
AI2_SENSITIVITY = {Sensitivity.WEAK: ("weak", 1), Sensitivity.MODERATE: ("moderate", 2),
                   Sensitivity.STRONG: ("strong", 3)}
CANVAS_UNIT_PX = 2   # the Form is 360 units wide on the 720 px panel (Form: formWidth = 360)
SEOUL = "loc 37.5665,126.9780"   # the first location preset, where SESSION_INIT puts the phone

pytestmark = [pytest.mark.device,
              pytest.mark.skipif(not all(p.exists() for p in (PROBE, TALK)),
                                 reason="3b acceptance fixtures live in D:\\OrcusDev\\fixtures")]


class LatestFrame:
    """The newest frame only: OrcusProbe's sensor labels redraw the screen all the time."""

    def __init__(self, ph: Phone) -> None:
        dev = ph.s.device()
        assert dev is not None
        self.dev = dev
        self.lock = threading.Lock()
        self.frame: Frame | None = None
        dev.frames.start(360, 640, self._on, lambda err: None)
        until(lambda: self.frame is not None, 10, "first frame")

    def _on(self, f: Frame) -> None:
        with self.lock:
            self.frame = f

    @property
    def latest(self) -> Frame:
        with self.lock:
            assert self.frame is not None
            return self.frame

    def stop(self) -> None:
        self.dev.frames.stop()


@pytest.fixture(scope="module")
def ph() -> Iterator[Phone]:
    compose.ensure_job()
    paths = compose.make_paths()
    print(f"\nlog: {compose.start_logging(paths, 'acceptance3b')}")
    s = Session(paths, compose.load_settings(paths), owner_kind="test")
    phone = Phone(s)
    s.engine.start()
    st = s.engine.wait_ready(900)
    print(f"phone READY: {st.mode} epoch {st.epoch} ({st.epoch_cause})")
    yield phone
    s.shutdown()


@pytest.fixture(scope="module", autouse=True)
def frames(ph: Phone) -> Iterator[LatestFrame]:
    """One stream for the whole module, as the window has: a tap or a wheel notch without a frame seq maps with
    the newest rotation, and a stopped stream kept an old one (a landscape test turned the next tests' taps)."""
    log = LatestFrame(ph)
    yield log
    log.stop()


def controls(ph: Phone) -> DeviceControls:
    dev = ph.s.device()
    assert dev is not None and dev.controls is not None
    return dev.controls


def probe(ph: Phone) -> str:
    """OrcusProbe in front, portrait, every permission granted (the location needs it), sensors reading."""
    c = controls(ph)
    if c.posture != Rotation.PORTRAIT:
        c.rotate(Rotation.PORTRAIT)
        time.sleep(2.0)
    ev, _ = ph.run(PROBE)
    assert ev.info is not None
    ph.s.apps.grant_all()
    time.sleep(1.0)
    ph.rerun()
    time.sleep(2.5)
    return ev.info.package


def count(text: str | None) -> int:
    assert text is not None, "label not on screen"
    return int(text.split()[-1])


def numbers(text: str | None) -> list[float]:
    """The values after the label's word: "touch2 31,40" is [31, 40], not [2, 31, 40]."""
    assert text is not None, "label not on screen"
    return [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", text.split(" ", 1)[-1])]


def set_sensitivity(ph: Phone, pkg: str, s: Sensitivity) -> None:
    name, value = AI2_SENSITIVITY[s]
    ph.tap(*ph.scroll_to(name, pkg).center)
    until(lambda: label(ph, pkg, "sens") == f"sens {value}", 10, f"sens {value}")


def shakes(ph: Phone, pkg: str, s: Sensitivity, clicks: int = 10) -> int:
    before = count(label(ph, pkg, "shake"))
    c = controls(ph)
    for _ in range(clicks):
        c.shake(s)
        time.sleep(0.7)   # one click: the pulse takes ~0.31 s, the app's MinimumInterval is 400 ms
    time.sleep(1.0)
    return count(label(ph, pkg, "shake")) - before


def mic_on(ph: Phone) -> bool:
    dev = ph.s.device()
    assert dev is not None
    stub = dev._stub   # type: ignore[attr-defined]  # noqa: SLF001  (the emulator's own state)
    return bool(stub.getMicrophoneState(empty_pb2.Empty(), timeout=5).realAudioEnabled)


# --- B3 -----------------------------------------------------------------------------------------------------------
@pytest.mark.timeout(900)
def test_b3_one_click_is_one_shaking_at_its_sensitivity(ph: Phone) -> None:
    pkg = probe(ph)
    for s in (Sensitivity.WEAK, Sensitivity.MODERATE, Sensitivity.STRONG):
        set_sensitivity(ph, pkg, s)
        got = shakes(ph, pkg, s)
        result(f"B3 shake {s.value} portrait", rang=f"{got}/10")
        assert 9 <= got <= 10, (s, got)
    set_sensitivity(ph, pkg, Sensitivity.MODERATE)
    c = controls(ph)
    for posture in (Rotation.REVERSE_PORTRAIT, Rotation.LANDSCAPE, Rotation.REVERSE_LANDSCAPE):
        before = count(label(ph, pkg, "shake"))
        c.rotate(posture)
        time.sleep(2.0)
        for _ in range(10):
            c.shake(Sensitivity.MODERATE)
            time.sleep(0.7)
        c.rotate(Rotation.PORTRAIT)   # labels are read in portrait: a landscape Form hides them
        time.sleep(2.0)
        got = count(label(ph, pkg, "shake")) - before
        result(f"B3 shake moderate {posture.name}", rang=f"{got}/10")
        assert 9 <= got <= 10, (posture, got)


@pytest.mark.timeout(600)
def test_b3_talktome_speaks_korean_and_answers_a_shake(ph: Phone) -> None:
    """OrcusProbe says 안녕하세요 with the Korean voice (AfterSpeaking true); TalkToMe (Moderate) answers a shake."""
    pkg = probe(ph)
    ph.tap(*ph.scroll_to("speak", pkg).center)
    until(lambda: label(ph, pkg, "tts") == "tts true", 30, "AfterSpeaking true")
    ph.run(TALK)
    time.sleep(2.0)
    spoke: list[str] = []   # App Inventor's InternalTextToSpeech: "InternalTTS speak called, message = ..."
    unsubscribe = ph.s.apps.subscribe_log(lambda e: spoke.append(e.msg) if "speak called" in e.msg else None)
    try:
        t = time.monotonic()
        controls(ph).shake(Sensitivity.MODERATE)
        until(lambda: any("Stop shaking me!" in m for m in spoke), 10, "TalkToMe speaking")
    finally:
        unsubscribe()
    result("B3 TalkToMe", korean="AfterSpeaking true", shake=f"spoke after {time.monotonic() - t:.1f} s")


# --- the turn method (§9.7) ---------------------------------------------------------------------------------
@pytest.mark.timeout(600)
def test_gradual_turns_are_not_a_shake_but_one_step_turns_are(ph: Phone) -> None:
    """S7: a turn in one step rang Strong (90°) and Moderate (180°). 15° every 0.1 s must ring nothing."""
    pkg = probe(ph)
    set_sensitivity(ph, pkg, Sensitivity.STRONG)
    c = controls(ph)
    before = count(label(ph, pkg, "shake"))
    t = time.monotonic()
    for to in (Rotation.LANDSCAPE, Rotation.REVERSE_PORTRAIT, Rotation.REVERSE_LANDSCAPE, Rotation.PORTRAIT,
               Rotation.REVERSE_PORTRAIT, Rotation.PORTRAIT):
        c.rotate(to)
        time.sleep(1.8)
    gradual = count(label(ph, pkg, "shake")) - before
    result("turns 15°/0.1 s at Strong", turns=6, rang=gradual, seconds=round(time.monotonic() - t, 1))
    c.rotate(Rotation.LANDSCAPE, gradual=False)
    time.sleep(1.5)
    c.rotate(Rotation.PORTRAIT)
    time.sleep(2.0)
    one_step = count(label(ph, pkg, "shake")) - before - gradual
    result("one-step 90° at Strong", rang=one_step)
    assert gradual == 0
    assert one_step >= 1   # the model is right: without the steps it rings


# --- B5 -----------------------------------------------------------------------------------------------------------
def display_degrees(ph: Phone) -> int:
    m = re.search(r"mCurrentRotation=ROTATION_(\d+)", ph.sh("dumpsys", "window", "displays"))
    assert m is not None
    return int(m.group(1))


def to_panel_from_display(degrees: int, dx: float, dy: float) -> tuple[float, float]:
    """The guest turns panel points into its display (S7: at 90°, panel (px, py) lands on display (py, 720 - px))."""
    match degrees:
        case 90:
            return W - dy, dx
        case 270:
            return dy, H - dx
        case _:
            return dx, dy


def pointer_for_panel(frame: Rotation, px: float, py: float) -> tuple[float, float]:
    """The inverse of device.rotation.to_panel (§9.2): where on the shown frame that panel point is."""
    match frame:
        case Rotation.LANDSCAPE:
            return py / H, 1 - px / W
        case Rotation.REVERSE_PORTRAIT:
            return 1 - px / W, 1 - py / H
        case Rotation.REVERSE_LANDSCAPE:
            return 1 - py / H, px / W
        case _:
            return px / W, py / H


def canvas(ph: Phone, pkg: str) -> Node:
    """The Canvas: the app's biggest plain View (labels are TextViews, the WebViewer a WebView)."""
    views = [n for n in ph.dump(pkg) if n.pkg == pkg and n.cls == "android.view.View"
             and n.bounds[3] - n.bounds[1] > 100]
    assert views, "no Canvas in the dump"
    return max(views, key=lambda n: (n.bounds[2] - n.bounds[0]) * (n.bounds[3] - n.bounds[1]))


def scroll_top(ph: Phone) -> None:
    """Scroll the Form to its top with the wheel: `input swipe` coordinates would be portrait ones."""
    dev = ph.s.device()
    assert dev is not None
    dev.input.wheel(PointerAt(0.5, 0.5, 0), 120 * 12)
    time.sleep(1.0)


def read_label(ph: Phone, pkg: str, word: str) -> str | None:
    """A label that a landscape Form may push below the screen: scroll down for it, then back to the top."""
    text = label(ph, pkg, word)
    dev = ph.s.device()
    assert dev is not None
    tries = 0
    while text is None and tries < 4:
        dev.input.wheel(PointerAt(0.5, 0.5, 0), -120 * 2)
        time.sleep(0.8)
        text = label(ph, pkg, word)
        tries += 1
    if tries:
        scroll_top(ph)
    return text


def tap_canvas(ph: Phone, frames: LatestFrame, pkg: str, node: Node, word: str, fx: float, fy: float) -> float:
    """Tap the Canvas at (fx, fy) of its visible box; the error of the Touched point in panel px."""
    x1, y1, x2, y2 = node.bounds
    dx, dy = x1 + fx * (x2 - x1), y1 + fy * (y2 - y1)
    px, py = to_panel_from_display(display_degrees(ph), dx, dy)
    f = frames.latest
    u, v = pointer_for_panel(f.rotation, px, py)
    dev = ph.s.device()
    assert dev is not None
    at = PointerAt(u, v, f.seq)
    dev.input.pointer(at, True)
    time.sleep(0.06)
    dev.input.pointer(at, False)
    time.sleep(0.8)
    got = numbers(read_label(ph, pkg, word))
    want = ((dx - x1) / CANVAS_UNIT_PX, (dy - y1) / CANVAS_UNIT_PX)
    return max(abs(got[0] - want[0]), abs(got[1] - want[1])) * CANVAS_UNIT_PX


@pytest.mark.timeout(900)
def test_b5_taps_land_within_4_px_in_every_posture(ph: Phone, frames: LatestFrame) -> None:
    pkg = probe(ph)
    c = controls(ph)
    try:
        for posture in (Rotation.PORTRAIT, Rotation.LANDSCAPE, Rotation.REVERSE_PORTRAIT,
                        Rotation.REVERSE_LANDSCAPE):
            c.rotate(posture)
            until(lambda: frames.latest.rotation == posture, 5, f"{posture.name} frames")
            time.sleep(1.5)                        # the display settles
            scroll_top(ph)
            node = canvas(ph, pkg)
            errors = [tap_canvas(ph, frames, pkg, node, "touch", fx, fy) for fx, fy in ((0.3, 0.4), (0.7, 0.6))]
            result(f"B5 tap {posture.name}", display=display_degrees(ph), canvas=node.bounds,
                   error_px=[round(e, 1) for e in errors])
            assert max(errors) <= 4, (posture, errors)
    finally:
        c.rotate(Rotation.PORTRAIT)
        time.sleep(1.5)


@pytest.mark.timeout(600)
def test_b5_landscape_screen2_gets_the_banner_and_match_orientation_fixes_it(ph: Phone,
                                                                             frames: LatestFrame) -> None:
    pkg = probe(ph)
    c = controls(ph)
    button = ph.scroll_to("open Screen2", pkg)
    m = ph.mark()
    t = time.monotonic()
    ph.tap(*button.center)
    ph.wait(lambda e: ErrorCode.APP_ORIENTATION_MISMATCH in e.warnings, m, 20)
    banner_s = time.monotonic() - t
    assert c.posture == Rotation.PORTRAIT and display_degrees(ph) == 90   # Screen2 alone turned the display
    m = ph.mark()
    ph.s.apps.match_orientation()
    ph.wait(lambda e: e.stage is RunStage.RUNNING and e.warnings == (), m, 20)
    assert c.posture == Rotation.LANDSCAPE
    until(lambda: frames.latest.rotation == Rotation.LANDSCAPE, 5, "landscape frames")
    node = canvas(ph, pkg)
    error = tap_canvas(ph, frames, pkg, node, "touch2", 0.5, 0.5)
    result("B5 Screen2", banner_s=round(banner_s, 1), tap_error_px=round(error, 1))
    assert error <= 4
    dev = ph.s.device()
    assert dev is not None
    m = ph.mark()
    dev.input.key("GoBack")                         # close Screen2: Screen1 follows the landscape posture
    time.sleep(5.0)
    assert not [e for e in ph.events[m:] if ErrorCode.APP_ORIENTATION_MISMATCH in e.warnings]
    c.rotate(Rotation.PORTRAIT)
    time.sleep(1.5)


# --- B6 -----------------------------------------------------------------------------------------------------------
def paste_into(ph: Phone, pkg: str, hint: str, text: str) -> str | None:
    n = ph.scroll_to(hint, pkg)
    ph.tap(*n.center)
    time.sleep(1.2)                                 # the keyboard comes up and the window may pan
    n2 = next((x for x in ph.dump(pkg) if x.text == hint), n)
    dev = ph.s.device()
    assert dev is not None
    dev.input.paste_text(text)
    time.sleep(1.2)
    box = next((x for x in ph.dump(pkg) if x.cls == "android.widget.EditText"
                and abs(x.bounds[1] - n2.bounds[1]) < 40 and abs(x.bounds[0] - n2.bounds[0]) < 40), None)
    dev.input.key("GoBack")                         # hide the keyboard
    time.sleep(0.8)
    return box.text if box is not None else None


@pytest.mark.timeout(600)
def test_b6_korean_goes_into_text_boxes_and_numbers_only_filters_it(ph: Phone) -> None:
    pkg = probe(ph)
    plain = paste_into(ph, pkg, "plain", "안녕하세요 Orcus")
    multi = paste_into(ph, pkg, "multi", "안녕하세요\nOrcus")
    number = paste_into(ph, pkg, "number", "안녕123")
    result("B6", plain=plain, multi=repr(multi), number=number)
    assert plain == "안녕하세요 Orcus"
    assert multi == "안녕하세요\nOrcus"
    assert number == "123"


# --- B13 ----------------------------------------------------------------------------------------------------------
@pytest.mark.timeout(600)
def test_b13_the_start_place_and_presets_reach_the_app_within_10_s(ph: Phone) -> None:
    """The launch sends the start place again (test providers report only when told, 3b); presets follow."""
    t = time.monotonic()
    pkg = probe(ph)
    until(lambda: label(ph, pkg, "loc") == SEOUL, 10, "the start place without touching anything")
    result("B13 location at launch", place="서울시청", seconds_after_probe=round(time.monotonic() - t, 1))
    for lat, lon, name in ((35.1798, 129.0750, "부산시청"), (37.5512, 126.9882, "남산서울타워")):
        t = time.monotonic()
        controls(ph).set_location(lat, lon)
        want = f"loc {lat:.4f},{lon:.4f}"
        until(lambda: label(ph, pkg, "loc") == want, 10, want)
        result("B13 location", place=name, seconds=round(time.monotonic() - t, 1))


@pytest.mark.timeout(600)
def test_b13_tilt_moves_the_orientation_sensor(ph: Phone) -> None:
    pkg = probe(ph)
    c = controls(ph)
    level = numbers(label(ph, pkg, "orient"))
    c.tilt(15.0, 0.0)
    time.sleep(1.5)
    forward = numbers(label(ph, pkg, "orient"))
    c.tilt(0.0, -15.0)
    time.sleep(1.5)
    sideways = numbers(label(ph, pkg, "orient"))
    c.tilt(0.0, 0.0)
    result("B13 tilt", level=level, forward=forward, sideways=sideways)
    assert abs(forward[1] - level[1]) >= 10 or abs(forward[2] - level[2]) >= 10
    assert abs(sideways[2] - level[2]) >= 10 or abs(sideways[1] - level[1]) >= 10


@pytest.mark.timeout(600)
def test_b13_microphone_consent_is_applied_at_every_launch(ph: Phone) -> None:
    pkg = probe(ph)
    assert not mic_on(ph)
    saved = ph.s.settings()
    ph.s._settings = dataclasses.replace(saved, privacy=PrivacySettings((pkg,)))   # noqa: SLF001  (not saved)
    try:
        ph.rerun()
        time.sleep(1.0)
        assert controls(ph).microphone and mic_on(ph)
        ph.s._settings = saved   # noqa: SLF001
        ph.rerun()
        time.sleep(1.0)
        assert not mic_on(ph)
    finally:
        ph.s._settings = saved   # noqa: SLF001
    result("B13 microphone", consent="on at launch, off for the next launch without it")


# --- wheel (§9.3, 3b) ---------------------------------------------------------------------------------------------
@pytest.mark.timeout(600)
def test_wheel_scrolls_the_screen_and_never_drags_the_canvas(ph: Phone) -> None:
    pkg = probe(ph)
    dev = ph.s.device()
    assert dev is not None
    scroll_top(ph)
    top = next(n for n in ph.dump(pkg) if n.text.startswith("shake ")).bounds[1]
    drags = count(label(ph, pkg, "drag"))
    dev.input.wheel(PointerAt(0.5, 0.8, 0), -120 * 2)   # two notches down, below the Canvas
    time.sleep(1.2)
    down = next(n for n in ph.dump(pkg) if n.text.startswith("shake ")).bounds[1]
    dev.input.wheel(PointerAt(0.5, 0.3, 0), 120 * 2)    # two notches up, over the Canvas
    time.sleep(1.2)
    back = next(n for n in ph.dump(pkg) if n.text.startswith("shake ")).bounds[1]
    result("wheel", top=top, after_down=down, after_up_over_canvas=back)
    assert top - down >= 200 and abs(back - top) <= 8
    assert count(label(ph, pkg, "drag")) == drags   # the Canvas saw no drag
