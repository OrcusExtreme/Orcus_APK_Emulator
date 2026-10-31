"""Phase 3a acceptance on the real phone (plan.md §3: B1, B2, B4, B8, B11; ARCHITECTURE.md §12.3 items 6, 10, 11, 12, 15).
B12 is test_apk_real.py; B7, B9 and the packaging smoke need the window and are scripted separately.

Run: py tools/dev.py test -m device tests/device/test_acceptance_3a.py -s
Needs D:\\OrcusDev\\fixtures: HelloPurr, TalkToMe, OrcusProbe, PaintPot, BallBounce, MoleMash, HelloPurrKo,
HelloPurrLand, HelloPurrMin36 (built in the App Inventor web IDE). The Drive-folder test runs only with
ORCUS_DRIVE_DIR set to a Google Drive folder: it uploads a copy there and deletes it (it lands in Drive's trash). The GPU test rebuilds the phone twice (software, then host):
about 7 minutes. Lines starting with RESULT are the measurements for plan.md §4.
"""

import json
import os
import shutil
import socket
import statistics
import subprocess
import threading
import time
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from conftest import REPO
from orcus.app import compose
from orcus.app.session import Session
from orcus.common import messages_ko, winjob
from orcus.contracts import (
    EngineState,
    EpochCause,
    ErrorCode,
    Frame,
    GpuMode,
    HwKey,
    OrcusError,
    PointerAt,
    Rotation,
    RunPolicy,
    RunStage,
    SessionMode,
)
from test_apk_real import FIX, HELLO, PROBE, H, Phone, W

PAINT, BALL, LAND, KO, MIN36 = (FIX / f"{n}.apk"
                                for n in ("PaintPot", "BallBounce", "HelloPurrLand", "HelloPurrKo", "HelloPurrMin36"))
B1_APPS = [FIX / f"{n}.apk" for n in ("HelloPurr", "TalkToMe", "PaintPot", "BallBounce", "MoleMash")]
OUT = Path(r"D:\OrcusDev\pytest")

pytestmark = [pytest.mark.device,
              pytest.mark.skipif(not all(p.exists() for p in (*B1_APPS, PROBE, LAND, KO, MIN36)),
                                 reason="3a acceptance fixtures live in D:\\OrcusDev\\fixtures")]


def result(name: str, **values: object) -> None:
    print(f"\nRESULT {name}: " + ", ".join(f"{k}={v}" for k, v in values.items()))


def until(pred: Callable[[], bool], timeout_s: float, what: str) -> None:
    end = time.monotonic() + timeout_s
    while not pred():
        assert time.monotonic() < end, f"timed out: {what}"
        time.sleep(0.2)


def at(x: float, y: float) -> PointerAt:
    return PointerAt(x / W, y / H, 0)   # portrait panel pixels; no frame stream -> the latest rotation


def drag(ph: Phone, a: tuple[float, float], b: tuple[float, float], steps: int, seconds: float) -> None:
    dev = ph.s.device()
    assert dev is not None
    dev.input.pointer(at(*a), True)
    for i in range(1, steps + 1):
        time.sleep(seconds / steps)
        dev.input.pointer(at(a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps), True)
    dev.input.pointer(at(*b), False)


def tap(ph: Phone, x: float, y: float) -> None:
    dev = ph.s.device()
    assert dev is not None
    dev.input.pointer(at(x, y), True)
    time.sleep(0.05)
    dev.input.pointer(at(x, y), False)


def screenshot(ph: Phone, name: str | None = None) -> QImage:
    dev = ph.s.device()
    assert dev is not None
    got: list[bytes | None] = []
    done = threading.Event()

    def on_done(png: bytes | None) -> None:
        got.append(png)
        done.set()
    dev.screenshot_png(on_done)
    assert done.wait(20) and got[0], "no screenshot"
    if name:
        (OUT / name).write_bytes(got[0])
    img = QImage.fromData(got[0])
    assert not img.isNull()
    return img


def is_red(img: QImage, x: int, y: int, r: int = 3) -> bool:
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            c = img.pixelColor(x + dx, y + dy)
            if c.red() > 180 and c.green() < 90 and c.blue() < 90:
                return True
    return False


def focus(ph: Phone) -> str:
    return next((x.strip() for x in ph.sh("dumpsys", "window").splitlines() if "mCurrentFocus=" in x), "")


def label(ph: Phone, pkg: str, word: str) -> str | None:
    return next((n.text for n in ph.dump(pkg) if n.text.startswith(word + " ")), None)


class FrameLog:
    """The session's frame stream at half size (360x640 portrait): (arrival, frame) pairs."""

    def __init__(self, ph: Phone, w: int = 360, h: int = 640) -> None:
        dev = ph.s.device()
        assert dev is not None
        self.dev = dev
        self.items: list[tuple[float, Frame]] = []
        self.lock = threading.Lock()
        dev.frames.start(w, h, self._on, lambda err: None)
        until(lambda: bool(self.items), 10, "first frame")

    def _on(self, f: Frame) -> None:
        with self.lock:
            self.items.append((time.time(), f))

    def since(self, t: float) -> list[tuple[float, Frame]]:
        with self.lock:
            return [(a, f) for a, f in self.items if a >= t]

    def stop(self) -> None:
        self.dev.frames.stop()


def rgb(f: Frame, x: int, y: int) -> tuple[int, int, int]:
    i = (y * f.width + x) * 3
    return f.rgb888[i], f.rgb888[i + 1], f.rgb888[i + 2]


def blue_centroid(f: Frame) -> tuple[float, float] | None:
    xs, ys = [], []
    for y in range(0, f.height, 2):
        row = y * f.width * 3
        for x in range(0, f.width, 2):
            i = row + x * 3
            if f.rgb888[i + 2] > 200 and f.rgb888[i] < 60 and f.rgb888[i + 1] < 60:   # not the bar's 3F51B5
                xs.append(x)
                ys.append(y)
    return (statistics.fmean(xs), statistics.fmean(ys)) if len(xs) > 5 else None


@pytest.fixture(scope="module")
def ph() -> Iterator[Phone]:
    compose.ensure_job()
    paths = compose.make_paths()
    print(f"\nlog: {compose.start_logging(paths, 'acceptance')}")
    s = Session(paths, compose.load_settings(paths), owner_kind="test")
    phone = Phone(s)
    s.engine.start()
    st = s.engine.wait_ready(900)
    print(f"phone READY: {st.mode} epoch {st.epoch} ({st.epoch_cause})")
    yield phone
    s.shutdown()


def wait_state(ph: Phone, *states: EngineState, timeout_s: float = 60) -> None:
    until(lambda: ph.s.engine.status().state in states, timeout_s, f"engine {states}")


# --- B1 -----------------------------------------------------------------------------------------------------------
@pytest.mark.timeout(600)
def test_b1_drop_to_interaction(ph: Phone) -> None:
    rows = []
    for apk in B1_APPS:
        m = ph.mark()
        rid = ph.s.apps.submit(apk, RunPolicy.FRESH_APP)
        card = ph.wait(lambda e, r=rid: e.run_id == r and e.stage is RunStage.INSPECTING, m, 30)
        end = ph.wait(lambda e, r=rid: e.run_id == r and e.stage in (RunStage.RUNNING, RunStage.FAILED), m, 90)
        assert end.stage is RunStage.RUNNING, end.problem
        rows.append((apk.stem, card.elapsed_s, end.elapsed_s))
    result("B1 warm engine, drop -> running (5 apps, FRESH_APP)",
           **{name: f"{run:.1f}s (card {card:.2f}s)" for name, card, run in rows})
    assert max(r[2] for r in rows) <= 10.0 and max(r[1] for r in rows) <= 1.0


@pytest.mark.timeout(600)
def test_b1_drop_right_after_start(ph: Phone) -> None:
    ph.s.engine.stop()
    wait_state(ph, EngineState.STOPPED)
    t = time.monotonic()
    ph.s.engine.start()
    m = ph.mark()
    rid = ph.s.apps.submit(HELLO, RunPolicy.FRESH_APP)
    end = ph.wait(lambda e: e.run_id == rid and e.stage in (RunStage.RUNNING, RunStage.FAILED), m, 180)
    assert end.stage is RunStage.RUNNING, end.problem
    result("B1 start + drop at once", drop_to_running=f"{end.elapsed_s:.1f}s",
           start_to_running=f"{time.monotonic() - t:.1f}s")
    assert end.elapsed_s <= 20.0


# --- B4 -----------------------------------------------------------------------------------------------------------
@pytest.mark.timeout(300)
def test_b4_back_home_recents(ph: Phone) -> None:
    ev, _ = ph.run(PROBE, RunPolicy.FRESH_APP)
    assert ev.info is not None
    pkg = ev.info.package
    time.sleep(2)
    ph.answer_permission(True, pkg)
    dev = ph.s.device()
    assert dev is not None
    dev.input.key(HwKey.BACK)
    until(lambda: label(ph, pkg, "back") == "back 1", 15, "BackPressed")
    dev.input.key(HwKey.HOME)
    until(lambda: "launcher" in focus(ph).lower(), 10, "home screen")
    home = focus(ph)
    ph.rerun()
    until(lambda: pkg in focus(ph), 10, "probe in front")
    dev = ph.s.device()
    assert dev is not None
    dev.input.key(HwKey.RECENTS)
    until(lambda: pkg not in focus(ph), 10, "recents over the app")
    result("B4", back="BackPressed -> 'back 1'", home=home.split()[-1][:80], recents=focus(ph).split()[-1][:80])


# --- B2 -----------------------------------------------------------------------------------------------------------
@pytest.mark.timeout(300)
def test_b2_probe_drag_density(ph: Phone) -> None:
    ev = ph.rerun()   # the Probe again, scrolled to the top: Canvas1 spans roughly y 160-760
    assert ev.info is not None
    pkg = ev.info.package
    time.sleep(1.5)
    drag(ph, (200, 450), (500, 450), steps=30, seconds=0.5)   # 300 px at ~60 Hz
    time.sleep(1.0)
    text = label(ph, pkg, "drag") or "drag 0"
    result("B2 Probe 300 px drag", dragged=text, moves_sent=30)
    assert int(text.split()[1]) >= 20


@pytest.mark.timeout(300)
def test_b2_paintpot_line_latency_and_frame_age(ph: Phone) -> None:
    ph.run(PAINT, RunPolicy.FRESH_APP)
    time.sleep(1.5)
    frames = FrameLog(ph)
    try:
        lat = []
        for x, y in ((360, 700), (200, 900), (520, 900)):   # mouse -> screen: tap, first frame showing the dot
            t0 = time.time()
            tap(ph, x, y)
            until(lambda: any(rgb(f, x // 2, y // 2)[0] > 180 and rgb(f, x // 2, y // 2)[1] < 90
                              for _a, f in frames.since(t0)), 5, "dot on screen")
            first = next(a for a, f in frames.since(t0) if rgb(f, x // 2, y // 2)[0] > 180
                         and rgb(f, x // 2, y // 2)[1] < 90)
            lat.append((first - t0) * 1000)
            time.sleep(0.3)
        t1 = time.time()
        drag(ph, (100, 600), (620, 600), steps=30, seconds=0.6)
        t2 = time.time()
        time.sleep(1.0)
        ages = sorted((a - f.ts_us / 1e6) * 1000 for a, f in frames.since(t1))
        fps = sum(1 for a, _f in frames.since(t1) if a <= t2) / max(1e-3, t2 - t1)
    finally:
        frames.stop()
    img = screenshot(ph, "paintpot_line.png")
    missing = [x for x in range(120, 600) if not is_red(img, x, 600)]
    p95 = ages[int(0.95 * (len(ages) - 1))] if ages else -1
    result("B2 PaintPot", line_missing_columns=len(missing), latency_ms=[round(v) for v in lat],
           frame_age_p95_ms=round(p95, 1), frames=len(ages), fps_during_drag=round(fps, 1))
    assert not missing, f"the line breaks at x={missing[:10]}"
    assert max(lat) <= 100, "mouse -> screen over the 100 ms budget (S2)"
    assert p95 <= 50, "frame age p95 over 50 ms (S2)"


@pytest.mark.timeout(300)
def test_b2_ballbounce_follows_the_mouse(ph: Phone) -> None:
    speeds: list[float] = []
    for i, seconds in enumerate((0.25, 0.08)):   # the same 240 px at ~1,000 then ~3,000 px/s (0.6 s was no fling)
        if i == 0:
            ph.run(BALL, RunPolicy.FRESH_APP)
        else:
            ph.rerun()
        time.sleep(1.5)
        frames = FrameLog(ph)
        try:
            c = blue_centroid(frames.items[-1][1])
            assert c is not None, "ball not found"
            bx, by = c[0] * 2, c[1] * 2
            t0 = time.time()
            drag(ph, (bx, by), (bx + 240, by), steps=8, seconds=seconds)
            time.sleep(1.2)
            track = [(f.ts_us / 1e6, blue_centroid(f)) for _a, f in frames.since(t0 + seconds)]
        finally:
            frames.stop()
        pts = [(t, p) for t, p in track if p is not None]
        if len(pts) < 3:   # no frames: the screen stood still, the ball never moved
            speeds.append(0.0)
            continue
        (ta, pa), (tb, pb) = pts[0], pts[-1]
        speeds.append(abs(pb[0] - pa[0]) * 2 / max(1e-3, tb - ta))
    result("B2 BallBounce", slower_mouse_ball_px_s=round(speeds[0]), faster_mouse_ball_px_s=round(speeds[1]))
    assert speeds[0] > 0, "the slower fling did not move the ball"
    assert speeds[1] > 1.5 * speeds[0], "the ball does not follow the mouse speed"


# --- §12.3 item 6 ---------------------------------------------------------------------------------------------------
@pytest.mark.timeout(300)
def test_item6_no_stuck_finger_after_the_input_stream_breaks(ph: Phone) -> None:
    ph.run(PAINT, RunPolicy.KEEP_DATA)   # same file: relaunch, empty canvas
    time.sleep(1.5)
    dev = ph.s.device()
    assert dev is not None
    dev.input.pointer(at(150, 500), True)
    for x in (170, 190, 210, 230, 250):
        time.sleep(0.03)
        dev.input.pointer(at(x, 500), True)
    dev._input.restart_stream()   # type: ignore[attr-defined]  # the stream breaks mid-drag
    time.sleep(1.0)
    tap(ph, 500, 900)
    time.sleep(1.0)
    img = screenshot(ph, "item6.png")
    on_path = [(312, 600), (375, 700), (437, 800)]   # on the segment a finger held at (250,500) would draw
    result("item 6", dot_at_tap=is_red(img, 500, 900), line_to_tap=[p for p in on_path if is_red(img, *p)])
    assert is_red(img, 500, 900) and not any(is_red(img, *p) for p in on_path)


# --- B8 -------------------------------------------------------------------------------------------------------------
def _zip(path: Path, names: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for n, data in names.items():
            z.writestr(n, data)
    return path


@pytest.mark.timeout(300)
def test_b8_wrong_files_and_a_korean_path(ph: Phone, tmp_path: Path) -> None:
    arm = tmp_path / "arm-only.apk"
    with zipfile.ZipFile(HELLO) as src, zipfile.ZipFile(arm, "w") as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item))
        dst.writestr("lib/armeabi-v7a/libdemo.so", b"\x7fELF" + bytes(60))
    cases = {
        arm: ErrorCode.INSTALL_NO_ABI,
        _zip(tmp_path / "game.apk", {"BundleConfig.pb": b"x", "base/manifest/AndroidManifest.xml": b"x"}):
            ErrorCode.FILE_IS_AAB,
        REPO / "tests" / "fixtures" / "OrcusProbe.aia": ErrorCode.FILE_IS_AIA,
        _zip(tmp_path / "ext.apk", {"com.example.ext/components.json": b"[]"}): ErrorCode.FILE_IS_AIX,
    }
    broken = tmp_path / "broken.apk"
    broken.write_bytes(HELLO.read_bytes()[:200_000])
    cases[broken] = ErrorCode.FILE_NOT_ANDROID
    seen = {}
    for path, code in cases.items():
        m = ph.mark()
        rid = ph.s.apps.submit(path)
        ev = ph.wait(lambda e, r=rid: e.run_id == r and e.stage in (RunStage.FAILED, RunStage.RUNNING), m, 60)
        assert ev.stage is RunStage.FAILED and ev.problem is not None and ev.problem.code is code, (path, ev)
        seen[path.name] = messages_ko.text(ev.problem.message_key)[:40]
    folder = tmp_path / "내 수업" / "한글 폴더"
    folder.mkdir(parents=True)
    korean = folder / "고양이 쓰다듬기.apk"
    shutil.copy(KO, korean)
    ev, _ = ph.run(korean, RunPolicy.FRESH_APP)
    assert ev.info is not None and ev.info.label == "고양이 쓰다듬기"
    result("B8 files", **seen, korean_path=f"running in {ev.elapsed_s:.1f}s, label {ev.info.label}")


# --- B8 / §12.3 item 7 ---------------------------------------------------------------------------------------------
@pytest.mark.timeout(300)
def test_b8_min_sdk_36_installs_and_runs(ph: Phone) -> None:
    ev, _ = ph.run(MIN36, RunPolicy.FRESH_APP)
    assert ev.info is not None and ev.info.min_sdk == 36   # a web IDE build that ignored AndroidMinSdk says 14
    api = ph.sh("getprop", "ro.build.version.sdk").strip()
    declared = next((x.strip() for x in ph.sh("dumpsys", "package", ev.info.package).splitlines()
                     if "minSdk=" in x), "")               # "versionCode=1 minSdk=36 targetSdk=36"
    result("B8 minSdk 36", running=f"{ev.elapsed_s:.1f}s", device_api=api, installed=declared)
    assert "minSdk=36" in declared


DRIVE = os.environ.get("ORCUS_DRIVE_DIR")


@pytest.mark.timeout(300)
@pytest.mark.skipif(not DRIVE, reason="set ORCUS_DRIVE_DIR to a Google Drive folder; the test uploads a copy there")
def test_b8_korean_apk_in_a_drive_folder(ph: Phone) -> None:
    assert DRIVE is not None
    folder = Path(DRIVE) / "Orcus 시험"
    folder.mkdir()                                         # never reuse (and later delete) a folder that was there
    apk = folder / "고양이 쓰다듬기.apk"
    try:
        shutil.copy(KO, apk)
        ev, _ = ph.run(apk, RunPolicy.FRESH_APP)           # right after the copy, while Drive may still upload it
        assert ev.info is not None and ev.info.label == "고양이 쓰다듬기"
        result("B8 Drive folder", path=apk, running=f"{ev.elapsed_s:.1f}s", label=ev.info.label)
    finally:
        for _ in range(20):                                # Drive may hold the new file for a moment
            try:
                apk.unlink(missing_ok=True)
                folder.rmdir()
                break
            except OSError:
                time.sleep(0.5)
    assert not folder.exists(), f"left behind: {folder}"


# --- B11 / §12.3 item 12 -------------------------------------------------------------------------------------------
@pytest.mark.timeout(300)
def test_b11_second_owner_gets_engine_in_use(ph: Phone) -> None:
    paths = compose.make_paths()
    other = Session(paths, compose.load_settings(paths), owner_kind="test2")
    err: OrcusError | None = None
    try:
        other.engine.start()
        until(lambda: other.engine.status().state == EngineState.FAILED, 30, "second owner refused")
        err = other.engine.status().error
        assert ph.sh("echo", "ok").strip() == "ok" and ph.s.engine.status().state == EngineState.READY
    finally:
        other.shutdown(fast=True)
    assert err is not None and err.code is ErrorCode.ENGINE_IN_USE
    result("B11 ownership", second_owner=str(err.code), first_vm="still READY")


# --- §12.3 item 11 --------------------------------------------------------------------------------------------------
def _port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


@pytest.mark.timeout(300)
def test_item11_adb_isolation_and_our_server_coming_back(ph: Phone, tmp_path: Path) -> None:
    paths = compose.make_paths()
    owner = json.loads(paths.state_file("engine_owner.json").read_text(encoding="utf-8"))
    port, serial = int(owner["adb_server"]), ph.s.engine.shell().serial
    adb = str(paths.adb_exe)

    def devices(p: int, env: dict[str, str] | None = None) -> list[str]:
        out = subprocess.run([adb, "-H", "127.0.0.1", "-P", str(p), "devices"], capture_output=True, text=True,
                             timeout=30, env=env).stdout
        return [line.split()[0] for line in out.splitlines()[1:] if line.strip()]
    assert devices(port) == [serial]                       # no USB devices, no other emulators
    env = {**os.environ, "ANDROID_USER_HOME": str(tmp_path / ".android")}   # keep keys off the profile
    started = _port_free(5037)
    if started:
        subprocess.run([adb, "-P", "5037", "start-server"], capture_output=True, timeout=60, env=env)
    try:
        outside = devices(5037, env)
    finally:
        if started:
            subprocess.run([adb, "-P", "5037", "kill-server"], capture_output=True, timeout=30, env=env)
    assert serial not in outside
    epoch = ph.s.engine.status().epoch
    winjob.terminate_pid(int(owner["adb_server_pid"]))      # our server dies
    t = time.monotonic()
    until(lambda: serial in devices(port), 30, "our adb server and the device back")
    back_s = time.monotonic() - t
    until(lambda: ErrorCode.ENGINE_ADB_LOST not in ph.s.engine.status().warnings, 20, "banner cleared")
    st = ph.s.engine.status()
    assert (st.state, st.epoch) == (EngineState.READY, epoch)
    result("item 11", ours=[serial], default_5037=outside, started_5037=started, back_after_kill=f"{back_s:.1f}s")


# --- B11 crash restart --------------------------------------------------------------------------------------------
@pytest.mark.timeout(600)
def test_b11_crash_restart_reinstalls_the_last_app(ph: Phone) -> None:
    ev, m = ph.run(HELLO, RunPolicy.FRESH_APP)
    epoch = ph.s.engine.status().epoch
    sdk = compose.make_paths().sdk
    qemu = [pid for pid, image in winjob.processes_under(sdk) if "qemu-system" in image.lower()]
    assert qemu, "no qemu process"
    t = time.monotonic()
    winjob.terminate_pid(qemu[0])
    st = ph.s.engine.wait_ready(300, min_epoch=epoch + 1)
    assert st.epoch_cause is EpochCause.CRASH_RESTART
    again = ph.wait(lambda e: e.run_id == ev.run_id and e.stage is RunStage.RUNNING and e.epoch == st.epoch, m, 180)
    result("B11 crash restart", ready_again=f"{time.monotonic() - t:.1f}s", epoch=st.epoch,
           reinstalled_and_running=f"pid {again.pid}")


# --- §12.3 item 10 --------------------------------------------------------------------------------------------------
@pytest.mark.timeout(600)
def test_item10_busy_grpc_port_moves_to_the_next(ph: Phone) -> None:
    paths = compose.make_paths()
    saved = paths.state_file("ports.json")
    before = saved.read_bytes() if saved.exists() else None
    ph.s.engine.stop()
    wait_state(ph, EngineState.STOPPED)
    block = socket.socket()
    block.bind(("127.0.0.1", 8640))
    block.listen()
    try:
        ph.s.engine.start()
        ph.s.engine.wait_ready(300)
        grpc_port = json.loads(paths.state_file("engine_owner.json").read_text(encoding="utf-8"))["grpc"]
    finally:
        ph.s.engine.stop()
        wait_state(ph, EngineState.STOPPED)
        block.close()
        if before is None:
            saved.unlink(missing_ok=True)
        else:
            saved.write_bytes(before)
        ph.s.engine.start()
        ph.s.engine.wait_ready(300)
    result("item 10", busy=8640, chosen=grpc_port)
    assert grpc_port != 8640


# --- B8 / §12.3 item 15 --------------------------------------------------------------------------------------------
@pytest.mark.timeout(300)
def test_b8_landscape_launcher_turns_the_phone_first(ph: Phone) -> None:
    from orcus.apk import launch
    ev, _ = ph.run(LAND, RunPolicy.FRESH_APP)
    assert ev.info is not None and ev.info.launcher_orientation == "landscape"
    time.sleep(1.5)
    img = screenshot(ph, "landscape.png")                  # look at it: the kitty must stand upright
    display = launch.display_rotation(ph.s.engine.shell())
    frames = FrameLog(ph)
    try:
        rot = frames.items[-1][1].rotation
    finally:
        frames.stop()
    result("B8 landscape launcher", frame=rot.name, display=display.name if display else None,
           screenshot=f"{img.width()}x{img.height()}")
    assert rot is Rotation.LANDSCAPE and display is Rotation.LANDSCAPE and img.width() > img.height()


# --- B11 GPU mode software -> COLD (rebuilds twice; last) ---------------------------------------------------------
@pytest.mark.timeout(1800)
def test_b11_gpu_software_runs_cold_with_the_banner(ph: Phone) -> None:
    s = ph.s
    t = time.monotonic()
    epoch = s.engine.status().epoch
    found: dict[str, object] = {}
    try:
        assert s.update_settings({"engine.gpu_mode": "software"}).needs_rebuild
        s.engine.rebuild("acceptance B11: software")
        st = s.engine.wait_ready(900, min_epoch=epoch + 1)
        found["cold_ready"] = f"{time.monotonic() - t:.0f}s"
        found["warnings"] = [str(w) for w in st.warnings]
        found["renderer"] = st.caps.renderer if st.caps else None
        assert st.mode is SessionMode.COLD and ErrorCode.ENGINE_GPU_SOFTWARE in st.warnings
        assert st.caps is not None and st.caps.gpu_mode is GpuMode.SOFTWARE
        ev, _ = ph.run(HELLO, RunPolicy.FRESH_PHONE)       # COLD: FRESH_PHONE runs as FRESH_APP (§6.2)
        found["app"] = f"running in {ev.elapsed_s:.1f}s"
    finally:
        s.update_settings({"engine.gpu_mode": "host"})
        t2 = time.monotonic()
        s.engine.rebuild("acceptance B11: back to host")
        back = s.engine.wait_ready(900, min_epoch=s.engine.status().epoch + 1)
        found["back_to_golden"] = f"{time.monotonic() - t2:.0f}s"
    result("B11 GPU software", **found, mode_after=str(back.mode))
    assert back.mode is SessionMode.GOLDEN and ErrorCode.ENGINE_GPU_SOFTWARE not in back.warnings
