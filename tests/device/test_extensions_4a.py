"""Extensions on the real phone: light, proximity, battery, an incoming text and call, two fingers, the vibration
indicator, dropped pictures and sounds, the installed-app list and the camera picture. The App Inventor events are
read off OrcusWorld's labels.

Run: py tools/dev.py test -m device tests/device/test_extensions_4a.py -s
Needs D:\\OrcusDev\\fixtures\\OrcusWorld.apk (py tools/ai2/build_fixtures.py --build OrcusWorld) and HelloPurr.
Lines starting with RESULT are for plan.md §4.
"""

import re
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from ai2 import projects   # tools/ is on pytest's path (pyproject)

from orcus.app import compose
from orcus.app.session import Session
from orcus.contracts import DeviceControls, InstalledApp, MediaResult, PointerAt, RunStage
from test_acceptance_3a import label, result, until
from test_apk_real import FIX, HELLO, Phone, H, W

WORLD = FIX / "OrcusWorld.apk"
SENDER, CALLER = "01012345678", "01087654321"

pytestmark = [pytest.mark.device,
              pytest.mark.skipif(not (WORLD.exists() and HELLO.exists()),
                                 reason="OrcusWorld: py tools/ai2/build_fixtures.py --build OrcusWorld")]


@pytest.fixture(scope="module")
def ph() -> Iterator[Phone]:
    compose.ensure_job()
    paths = compose.make_paths()
    print(f"\nlog: {compose.start_logging(paths, 'extensions4a')}")
    s = Session(paths, compose.load_settings(paths), owner_kind="test")
    phone = Phone(s)
    s.engine.start()
    st = s.engine.wait_ready(900)
    print(f"phone READY: {st.mode} epoch {st.epoch} ({st.epoch_cause})")
    yield phone
    s.shutdown()


@pytest.fixture(scope="module")
def world(ph: Phone) -> str:
    """OrcusWorld in front with its permissions (Texting's RECEIVE_SMS, PhoneCall's phone state and call log)."""
    ev, _ = ph.run(WORLD)
    assert ev.info is not None
    ph.s.apps.grant_all()
    time.sleep(1.0)
    ph.rerun()   # PhoneCall listens once its permissions are granted, at Initialize
    time.sleep(2.5)
    return ev.info.package


def controls(ph: Phone) -> DeviceControls:
    dev = ph.s.device()
    assert dev is not None and dev.controls is not None
    return dev.controls


def digits(text: str | None) -> str:
    return re.sub(r"\D", "", text or "")


def wait(ph: Phone, pred: Callable[[], bool], timeout_s: float, what: str) -> None:
    """until(), with what the phone shows when it times out."""
    try:
        until(pred, timeout_s, what)
    except AssertionError as e:
        focus = next((x.strip() for x in ph.sh("dumpsys", "window").splitlines() if "mCurrentFocus=" in x), "")
        raise AssertionError(f"{e}\nfocus: {focus}\n{ph.evidence()}") from e


@pytest.mark.timeout(600)
def test_light_and_proximity_reach_the_app(ph: Phone, world: str) -> None:
    c = controls(ph)
    t = time.monotonic()
    c.set_light(1234.0)
    until(lambda: label(ph, world, "light") == "light 1234", 15, "LightChanged 1234")
    light_s = time.monotonic() - t
    c.set_proximity(0.0)
    until(lambda: label(ph, world, "near") == "near 0.0", 15, "ProximityChanged 0")
    c.set_proximity(1.0)
    until(lambda: label(ph, world, "near") == "near 1.0", 15, "ProximityChanged 1")
    c.set_light(0.0)
    until(lambda: label(ph, world, "light") == "light 0", 15, "LightChanged 0")
    result("light and proximity", light_changed_within_s=round(light_s, 1))


@pytest.mark.timeout(300)
def test_battery_level_and_charger_and_systemui_stays_up(ph: Phone) -> None:
    """Unplugged, not below 21 %: at 20 % the image's SystemUI crashed (Extreme Battery Saver's provider is missing);
    charging, any level."""
    c = controls(ph)
    sysui = ph.sh("pidof", "com.android.systemui").strip()
    c.set_battery(15, False)
    until(lambda: "level: 21" in ph.sh("dumpsys", "battery"), 15, "battery 21")
    out = ph.sh("dumpsys", "battery")
    assert "AC powered: false" in out and "status: 3" in out   # BATTERY_STATUS_DISCHARGING
    c.set_battery(5, True)
    until(lambda: "level: 5" in ph.sh("dumpsys", "battery"), 15, "battery 5 charging")
    assert "AC powered: true" in ph.sh("dumpsys", "battery")
    c.set_battery(100, True)
    until(lambda: "level: 100" in ph.sh("dumpsys", "battery"), 15, "battery 100")
    time.sleep(2.0)
    assert ph.sh("pidof", "com.android.systemui").strip() == sysui


@pytest.mark.timeout(600)
def test_a_text_reaches_texting_and_the_inbox(ph: Phone, world: str) -> None:
    text = "안녕 Orcus 문자"
    t = time.monotonic()
    mark = len(ph.log)
    controls(ph).receive_sms(SENDER, text)
    try:
        until(lambda: (label(ph, world, "sms") or "").endswith(text), 30, "MessageReceived")
    except AssertionError as e:
        focus = next((x.strip() for x in ph.sh("dumpsys", "window").splitlines() if "mCurrentFocus=" in x), "")
        sms = [x[:160] for x in list(ph.log)[mark:] if re.search(r"(?i)sms|texting|bugle", x)][:20]
        inbox = ph.sh("content", "query", "--uri", "content://sms/inbox", "--projection", "address:body")
        raise AssertionError(f"{e}\nfocus: {focus}\nlabel: {label(ph, world, 'sms')}\ninbox: {inbox[-300:]}\n"
                             + "\n".join(sms) + "\n" + ph.evidence()) from e
    got = label(ph, world, "sms")
    assert digits(got).endswith(SENDER[1:]), got
    inbox = ph.sh("content", "query", "--uri", "content://sms/inbox", "--projection", "address:body")
    assert text in inbox
    result("text", message_received_within_s=round(time.monotonic() - t, 1), label=got)


@pytest.mark.timeout(600)
def test_an_incoming_call_rings_in_phonecall_and_ends_missed(ph: Phone, world: str) -> None:
    c = controls(ph)
    c.call_in(CALLER)
    until(lambda: "mCallState=1" in ph.sh("dumpsys", "telephony.registry"), 20, "ringing")
    time.sleep(3.0)
    c.call_end(CALLER)
    until(lambda: "mCallState=0" in ph.sh("dumpsys", "telephony.registry"), 20, "idle again")
    until(lambda: (label(ph, world, "callend") or "").startswith("callend 1"), 30, "PhoneCallEnded 1 (missed)")
    started, ended = label(ph, world, "call"), label(ph, world, "callend")
    assert started is not None and started.startswith("call 1 ") and digits(started).endswith(CALLER[1:]), started
    assert digits(ended).endswith(CALLER[1:]), ended
    result("call", started=started, ended=ended)


def at(x: float, y: float) -> PointerAt:
    return PointerAt(x / W, y / H, 0)


@pytest.mark.timeout(600)
def test_two_fingers_spread_and_pinch_in_a_web_page(ph: Phone, world: str) -> None:
    web = next(n for n in ph.dump(world) if n.cls == "android.webkit.WebView")
    cx, cy = web.center
    dev = ph.s.device()
    assert dev is not None

    def fingers(start: float, end: float, steps: int = 12) -> None:
        for i in range(steps + 1):
            gap = start + (end - start) * i / steps
            dev.input.touches((at(cx - gap, cy), at(cx + gap, cy)))
            time.sleep(0.04)
        dev.input.touches(())
    fingers(60, 220)
    wait(ph, lambda: (label(ph, world, "pinch") or "").startswith("pinch 2 "), 15, "two fingers in the page")
    spread = float((label(ph, world, "pinch") or "0 0 0").split()[2])
    fingers(220, 60)
    wait(ph, lambda: label(ph, world, "pinch") != f"pinch 2 {spread:.2f}", 15, "pinched")
    pinched = float((label(ph, world, "pinch") or "0 0 0").split()[2])
    assert spread > 3.0 and pinched < 0.4, (spread, pinched)
    result("two fingers", spread=spread, pinched=pinched)


@pytest.mark.timeout(600)
def test_a_vibration_of_the_app_is_reported(ph: Phone) -> None:
    got: list[tuple[float, int]] = []
    unsubscribe = ph.s.apps.subscribe_vibration(lambda ms: got.append((time.monotonic(), ms)))
    try:
        ph.run(HELLO)
        time.sleep(2.0)
        assert got == []                          # vibrations before the launch are old
        t = time.monotonic()
        dev = ph.s.device()
        assert dev is not None
        dev.input.pointer(at(240, 489), True)     # the kitty: Sound.Play and Sound.Vibrate(500)
        time.sleep(0.08)
        dev.input.pointer(at(240, 489), False)
        wait(ph, lambda: bool(got), 10, "vibration reported")
        time.sleep(3.0)
        assert len(got) == 1, got                 # running, then listed as ended: one vibration
        result("vibration", reported_after_s=round(got[0][0] - t, 1), ms=got[0][1])
    finally:
        unsubscribe()


@pytest.mark.timeout(300)
def test_dropped_pictures_and_sounds_are_in_the_phones_media(ph: Phone, tmp_path: Path) -> None:
    """Through the window's path (open_paths): staged, pushed under the dropped names, listed by MediaStore."""
    photo, sound = tmp_path / "고양이 사진.png", tmp_path / "야옹 소리.wav"
    photo.write_bytes(projects.kitty_png())   # the fixtures' own drawing and sound (tools/ai2)
    sound.write_bytes(projects.meow_wav())
    got: list[MediaResult] = []
    unsubscribe = ph.s.apps.subscribe_media(got.append)
    try:
        t = time.monotonic()
        ph.s.open_paths([photo, sound])
        until(lambda: len(got) == 2, 60, "two results")
        assert [(r.phone_path, r.problem) for r in got] == [("/sdcard/Pictures/고양이 사진.png", None),
                                                           ("/sdcard/Music/야옹 소리.wav", None)]
        images = ph.sh("content", "query", "--uri", "content://media/external/images/media", "--projection",
                       "_display_name")
        audio = ph.sh("content", "query", "--uri", "content://media/external/audio/media", "--projection",
                      "_display_name")
        assert "고양이 사진.png" in images and "야옹 소리.wav" in audio
        result("dropped media", both_in_s=round(time.monotonic() - t, 1))
    finally:
        unsubscribe()


@pytest.mark.timeout(300)
def test_an_installed_app_runs_again_from_the_list(ph: Phone) -> None:
    """[App] tab: the list has the apps put in the phone; [Run] pulls the APK and goes the usual way."""
    lists: list[tuple[InstalledApp, ...]] = []
    unsubscribe = ph.s.apps.subscribe_apps(lists.append)
    try:
        ev, _ = ph.run(HELLO)
        assert ev.info is not None
        pkg = ev.info.package
        until(lambda: bool(lists) and any(a.package == pkg for a in lists[-1]), 15, "HelloPurr listed")
        names = sorted(a.name for a in lists[-1])
        m = ph.mark()
        t = time.monotonic()
        ph.s.apps.run_installed(pkg)
        again = ph.wait(lambda e: e.stage is RunStage.RUNNING and e.info is not None and e.info.package == pkg, m, 60)
        assert again.run_id != ev.run_id and "INSTALLING" not in ph.stages(again.run_id, m)   # same file: no install
        result("installed apps", listed=names, run_again_s=round(time.monotonic() - t, 1))
    finally:
        unsubscribe()


def camera_center(ph: Phone) -> tuple[int, int, int]:
    dev = ph.s.device()
    assert dev is not None
    got: list[bytes | None] = []
    dev.screenshot_png(got.append)
    until(lambda: bool(got), 20, "screenshot")
    img = QImage.fromData(got[0] or b"")
    c = img.pixelColor(img.width() // 2, img.height() * 9 // 20)   # the preview's middle, above the shutter bar
    return c.red(), c.green(), c.blue()


def is_blue(rgb: tuple[int, int, int]) -> bool:
    return rgb[2] > 180 and rgb[0] < 80


def is_red(rgb: tuple[int, int, int]) -> bool:
    return rgb[0] > 180 and rgb[2] < 80


@pytest.mark.timeout(300)
def test_the_camera_shows_the_chosen_picture_and_a_new_one_when_reopened(ph: Phone) -> None:
    """[Sensors] > Camera: the camera app (what Camera.TakePicture opens) shows the picture; a new one once reopened."""
    blue = projects._png(320, 240, lambda x, y: (20, 60, 230, 255))   # noqa: SLF001
    red = projects._png(320, 240, lambda x, y: (230, 30, 30, 255))    # noqa: SLF001
    try:
        ph.s.set_camera_image(blue)
        ph.sh("am", "start", "-a", "android.media.action.IMAGE_CAPTURE")
        until(lambda: is_blue(camera_center(ph)), 20, "blue in the camera")
        ph.s.set_camera_image(red)
        ph.sh("am", "force-stop", "com.android.camera2")
        ph.sh("am", "start", "-a", "android.media.action.IMAGE_CAPTURE")
        until(lambda: is_red(camera_center(ph)), 20, "red once reopened")
        result("camera picture", blue_then_red="ok")
    finally:
        ph.s.set_camera_image(None)
        ph.sh("am", "force-stop", "com.android.camera2")
