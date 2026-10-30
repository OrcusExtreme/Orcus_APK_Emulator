"""orcus.device on FakeEmulatorServer: rotation table, frames, input order and release, controls, logcat."""

import threading
import time

import pytest

from orcus.contracts import DeviceEndpoint, DeviceSession, Frame, HwKey, PointerAt, Rotation, Sensitivity
from orcus.device import controls, logcat, rotation
from orcus.device.session import GrpcDeviceSession
from orcus.emugrpc.fake import FakeEmulatorServer
from orcus.emugrpc.generated import emulator_controller_pb2 as ec

W, H = 720, 1280


@pytest.fixture
def fake():
    with FakeEmulatorServer(fps=60.0) as f:
        yield f


@pytest.fixture
def session(fake: FakeEmulatorServer):
    located: list[tuple[float, float, float]] = []
    s = GrpcDeviceSession(DeviceEndpoint(f"localhost:{fake.port}", fake.token, "emulator-5640", 1),
                          location=lambda lat, lon, alt: located.append((lat, lon, alt)))
    s.located = located   # type: ignore[attr-defined]
    yield s
    s.close()


def until(pred, timeout_s: float = 5.0) -> None:   # type: ignore[no-untyped-def]
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if pred():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met")


def test_session_satisfies_the_contract(session: GrpcDeviceSession) -> None:
    _: DeviceSession = session


@pytest.mark.parametrize("rot,u,v,expected", [
    (Rotation.PORTRAIT, 0.25, 0.5, (180, 640)),
    (Rotation.LANDSCAPE, 0.25, 0.5, (360, 320)),
    (Rotation.REVERSE_PORTRAIT, 0.25, 0.5, (540, 640)),
    (Rotation.REVERSE_LANDSCAPE, 0.25, 0.5, (360, 960)),
    (Rotation.PORTRAIT, 1.5, -1.0, (W - 1, 0)),
])
def test_rotation_table_confirmed_in_s7(rot: Rotation, u: float, v: float, expected: tuple[int, int]) -> None:
    assert rotation.to_panel(rot, u, v, W, H) == expected


def test_frames_use_format_size_and_follow_retarget(fake: FakeEmulatorServer, session: GrpcDeviceSession) -> None:
    got: list[Frame] = []
    ended: list[object] = []
    session.frames.start(432, 768, got.append, ended.append)
    until(lambda: len(got) >= 2)
    assert (got[-1].width, got[-1].height) == (432, 768) and len(got[-1].rgb888) == 432 * 768 * 3
    fake.rotation = ec.Rotation.LANDSCAPE
    session.frames.retarget(768, 432)
    until(lambda: got[-1].width == 768)
    assert got[-1].rotation == Rotation.LANDSCAPE
    assert session._frames.rotation_of(got[-1].seq) == Rotation.LANDSCAPE
    session.frames.retarget(2000, 4000)   # capped at 720x1280
    until(lambda: got[-1].height == 1280)
    session.frames.stop()
    n = len(got)
    time.sleep(0.2)
    assert len(got) <= n + 1 and ended == []


def mouse(fake: FakeEmulatorServer) -> list[tuple[int, int, int]]:
    return [(e.mouse_event.x, e.mouse_event.y, e.mouse_event.buttons) for e in fake.input_events
            if e.HasField("mouse_event")]


def keys(fake: FakeEmulatorServer) -> list[tuple[str, int]]:
    return [(e.key_event.key, e.key_event.eventType) for e in fake.input_events if e.HasField("key_event")]


def test_pointer_maps_with_the_clicked_frames_rotation(fake: FakeEmulatorServer, session: GrpcDeviceSession) -> None:
    got: list[Frame] = []
    fake.rotation = ec.Rotation.LANDSCAPE
    session.frames.start(768, 432, got.append, lambda e: None)
    until(lambda: bool(got))
    seq = got[-1].seq
    session.input.pointer(PointerAt(0.25, 0.5, seq), True)
    session.input.pointer(PointerAt(0.30, 0.5, seq), True)
    session.input.pointer(PointerAt(0.30, 0.5, seq), False)
    until(lambda: len(mouse(fake)) == 3)
    assert mouse(fake) == [(360, 320, 1), (360, 384, 1), (360, 384, 0)]


def test_release_all_lifts_a_held_finger_once(fake: FakeEmulatorServer, session: GrpcDeviceSession) -> None:
    session.input.pointer(PointerAt(0.5, 0.5, 0), True)
    session.input.release_all()
    session.input.release_all()                       # nothing held any more
    session.input.pointer(PointerAt(0.5, 0.5, 0), False)   # a stray release is dropped
    until(lambda: len(mouse(fake)) == 2)
    time.sleep(0.1)
    assert mouse(fake) == [(360, 640, 1), (360, 640, 0)]


def test_close_releases_a_held_finger(fake: FakeEmulatorServer) -> None:
    s = GrpcDeviceSession(DeviceEndpoint(f"localhost:{fake.port}", fake.token, "emulator-5640", 1))
    s.input.pointer(PointerAt(0.1, 0.1, 0), True)
    s.close()
    until(lambda: len(mouse(fake)) == 2)
    assert mouse(fake)[-1][2] == 0


def test_keys_chords_and_paste_order(fake: FakeEmulatorServer, session: GrpcDeviceSession) -> None:
    kp, down, up = ec.KeyboardEvent.keypress, ec.KeyboardEvent.keydown, ec.KeyboardEvent.keyup
    session.input.key(HwKey.BACK)
    session.input.chord("Control", "a")
    session.input.paste_text("안녕하세요 Orcus")
    session.input.type_ascii("abc")   # ASCII goes through paste too (S8)
    until(lambda: len(keys(fake)) == 6)
    assert keys(fake) == [("GoBack", kp), ("Control", down), ("a", kp), ("Control", up), ("Paste", kp),
                          ("Paste", kp)]
    assert fake.clipboard == "abc"
    assert [c for c, _ in fake.calls].count("setClipboard") == 2


def test_controls_shake_rotate_tilt_mic_location(fake: FakeEmulatorServer, session: GrpcDeviceSession) -> None:
    fake.sensors[ec.SensorValue.ACCELERATION] = ec.SensorValue(target=ec.SensorValue.ACCELERATION,
                                                                value=ec.ParameterValue(data=[0.0, -9.81, 0.0]))
    c = session.controls
    assert c is not None
    c.shake(Sensitivity.MODERATE)
    c.rotate(Rotation.LANDSCAPE)
    c.tilt(35, -5)
    c.set_microphone(True)
    c.set_location(37.5, 127.0)
    until(lambda: fake.microphone and session.located)   # type: ignore[attr-defined]
    pulse = fake.sensors[ec.SensorValue.ACCELERATION].value.data
    assert [round(v, 2) for v in pulse] == [0.0, -25.81, 0.0]   # upside down: away from zero
    rots = [list(p.value.data) for p in fake.physical]
    assert rots[0] == [0.0, 0.0, 0.0]                  # the shake ends by restoring the posture
    assert rots[1] == [0.0, 0.0, 90.0]
    assert rots[2] == [20.0, 0.0, 85.0]                # tilt clamped to 20°, roll around the current z
    assert session.located == [(37.5, 127.0, 30.0)]    # type: ignore[attr-defined]


def test_pulse_never_crosses_zero() -> None:
    assert [round(v, 2) for v in controls.pulse_for([0.0, 9.81, 0.3], 16)] == [0.0, 25.81, 0.3]
    assert [round(v, 2) for v in controls.pulse_for([-9.81, 0.1, 0.0], 9)] == [-18.81, 0.1, 0.0]


def test_logcat_parses_text_mode_and_drops_the_backlog(fake: FakeEmulatorServer, session: GrpcDeviceSession) -> None:
    now = time.time()
    old = time.strftime("%m-%d %H:%M:%S.000", time.localtime(now - 3600))
    new = time.strftime("%m-%d %H:%M:%S.123", time.localtime(now + 1))
    # as 37.2.12 sends it: one entry per message and no newline (seen on the device in Phase 3a)
    fake.logcat.put(f"{old}  100  100 E Form    : backlog line")
    fake.logcat.put(f"{new}  4242  4242 E Form    : Form Screen1 ErrorOccurred, errorNumber = 601")
    # two entries in one message, still without a newline between them
    fake.logcat.put(f"{new}  42  43 D InternalTTS: speak called when TTS not initialized"
                    f"{new}  42  43 D InternalTTS: delaying call to speak.  Retries is: 1\r\n")
    stream = session.logcat()
    out = []

    def consume() -> None:
        for entry in stream:
            out.append(entry)
            if len(out) == 3:
                return
    t = threading.Thread(target=consume, daemon=True)
    t.start()
    t.join(5)
    stream.cancel()
    assert [(e.pid, e.level, e.tag) for e in out] == [(4242, "E", "Form"), (42, "D", "InternalTTS"),
                                                      (42, "D", "InternalTTS")]
    assert out[0].msg.endswith("errorNumber = 601")
    assert out[1].msg == "speak called when TTS not initialized"
    assert out[2].msg == "delaying call to speak.  Retries is: 1"


def test_logcat_source_is_injected_and_falls_back_to_grpc(fake: FakeEmulatorServer) -> None:
    class Source:
        def __init__(self) -> None:
            self.cancelled = False

        def __iter__(self):   # type: ignore[no-untyped-def]
            return iter(())

        def cancel(self) -> None:
            self.cancelled = True
    made: list[Source] = []

    def good() -> Source:
        made.append(Source())
        return made[-1]

    def bad() -> Source:
        raise OSError("adb.exe missing")
    ep = DeviceEndpoint(f"localhost:{fake.port}", fake.token, "emulator-5640", 1)
    s = GrpcDeviceSession(ep, logcat_source=good)
    assert s.logcat() is made[0]
    s.close()
    assert made[0].cancelled   # the session ends what it handed out
    s = GrpcDeviceSession(ep, logcat_source=bad)
    assert isinstance(s.logcat(), logcat.LogcatStream)
    s.close()


def test_logcat_entries_split_where_the_next_timestamp_starts() -> None:
    a = "10-30 18:29:45.733   697   697 D CompatConfig: Adding: com.android.server.compat.config.Change@e9625d1"
    b = "10-30 18:29:45.734   697   698 I Other   : msg with 12-34 56:78 inside"
    assert logcat.split_entries(a + b) == [a, b]
    assert logcat.split_entries(a + "\n") == [a]
    assert logcat.split_entries("") == []


def test_logcat_parse_rejects_noise() -> None:
    assert logcat.parse("--------- beginning of main") is None
    e = logcat.parse("10-30 15:43:07.781  2263  2263 F DEBUG   : Abort message: 'x'", year=2026)
    assert e is not None and (e.pid, e.tid, e.level, e.tag) == (2263, 2263, "F", "DEBUG")


def test_screenshot_png_is_non_blocking(session: GrpcDeviceSession) -> None:
    got: list[bytes | None] = []
    session.screenshot_png(got.append)
    until(lambda: bool(got))
    assert got[0] is not None and got[0].startswith(b"\x89PNG")
