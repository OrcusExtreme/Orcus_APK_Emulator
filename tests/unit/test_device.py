"""orcus.device on FakeEmulatorServer: rotation table, frames, input order and release, controls, logcat."""

import threading
import time

import pytest

from orcus.contracts import DeviceEndpoint, DeviceSession, Frame, HwKey, PhoneWorld, PointerAt, Rotation, Sensitivity
from orcus.device import controls, logcat, rotation
from orcus.device.session import GrpcDeviceSession
from orcus.emugrpc.fake import BREAK_KEY, FakeEmulatorServer
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


def test_new_stream_lifts_a_finger_the_broken_one_held(fake: FakeEmulatorServer,
                                                       session: GrpcDeviceSession) -> None:
    session.input.pointer(PointerAt(0.25, 0.25, 0), True)
    session._input.restart_stream()                    # broken mid-drag (§12.3 item 6)
    session.input.pointer(PointerAt(0.75, 0.75, 0), True)
    session.input.pointer(PointerAt(0.75, 0.75, 0), False)
    until(lambda: len(mouse(fake)) == 4, 10)
    assert mouse(fake) == [(180, 320, 1), (180, 320, 0), (540, 960, 1), (540, 960, 0)]


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
    until(lambda: len(keys(fake)) == 5)
    session.input.type_ascii("abc")   # ASCII goes through paste too (S8)
    until(lambda: len(keys(fake)) == 6)
    assert keys(fake) == [("GoBack", kp), ("Control", down), ("a", kp), ("Control", up), ("Paste", kp),
                          ("Paste", kp)]
    assert fake.clipboard == "abc"
    assert [c for c, _ in fake.calls].count("setClipboard") == 2


def test_fast_typing_goes_as_one_paste_in_order(fake: FakeEmulatorServer, session: GrpcDeviceSession) -> None:
    """Characters typed while a paste is on its way: one setClipboard with all of them, in order (3a review)."""
    kp, down, up = ec.KeyboardEvent.keypress, ec.KeyboardEvent.keydown, ec.KeyboardEvent.keyup
    session.input.key(HwKey.POWER, long_press=True)   # holds the input thread 0.7 s while the rest queues up
    for ch in ("안", "녕", "a", "b", "하세요"):
        session.input.type_ascii(ch) if ch.isascii() else session.input.paste_text(ch)
    session.input.chord("Control", "z")
    session.input.paste_text("!")
    until(lambda: len(keys(fake)) == 7)
    assert keys(fake) == [("Power", down), ("Power", up), ("Paste", kp), ("Control", down), ("z", kp),
                          ("Control", up), ("Paste", kp)]
    clips = [arg for c, arg in fake.calls if c == "setClipboard"]
    assert clips == ["안녕ab하세요", "!"]


def test_a_broken_stream_loses_no_input_after_it(fake: FakeEmulatorServer, session: GrpcDeviceSession) -> None:
    """gRPC pulls a failed call's request iterator once more: that pull took the first input after a reconnect."""
    kp = ec.KeyboardEvent.keypress
    session.input.key(BREAK_KEY)   # the emulator ends the call
    until(lambda: (BREAK_KEY, kp) in keys(fake))
    time.sleep(0.05)               # inside the reconnect backoff
    session.input.key(HwKey.HOME)
    session.input.key(HwKey.BACK)
    until(lambda: len(keys(fake)) == 3)
    assert keys(fake) == [(BREAK_KEY, kp), ("GoHome", kp), ("GoBack", kp)]
    assert [c for c, _ in fake.calls].count("streamInputEvent") == 2


def test_wheel_notches_add_up_and_follow_the_posture(fake: FakeEmulatorServer) -> None:
    scrolls: list[tuple[int, int, float]] = []
    gate = threading.Event()

    def sink(x: int, y: int, amount: float) -> None:
        gate.wait(2)   # one adb command (~60 ms) at a time; notches meanwhile add up
        scrolls.append((x, y, amount))
    s = GrpcDeviceSession(DeviceEndpoint(f"localhost:{fake.port}", fake.token, "emulator-5640", 1), scroll=sink)
    try:
        s.input.wheel(PointerAt(0.5, 0.25, 0), -120)    # one notch toward the user: scroll down
        time.sleep(0.1)
        for _ in range(3):
            s.input.wheel(PointerAt(0.5, 0.75, 0), -120)
        s.input.wheel(PointerAt(0.5, 0.75, 0), 60)      # half a notch back (precise touchpads)
        gate.set()
        until(lambda: len(scrolls) == 2)
        assert scrolls == [(360, 320, -1.0), (360, 959, -2.5)]   # portrait display coordinates
    finally:
        s.close()


def test_controls_shake_rotate_tilt_mic_location(fake: FakeEmulatorServer, session: GrpcDeviceSession,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controls, "LOCATION_GAP_S", 0.05)
    fake.sensors[ec.SensorValue.ACCELERATION] = ec.SensorValue(target=ec.SensorValue.ACCELERATION,
                                                                value=ec.ParameterValue(data=[0.0, -9.81, 0.0]))
    c = session.controls
    assert c is not None
    assert (c.posture, c.microphone) == (Rotation.PORTRAIT, False)
    c.shake(Sensitivity.MODERATE)
    c.rotate(Rotation.LANDSCAPE)
    assert c.posture == Rotation.LANDSCAPE   # at once: the caller decides the next turn from it
    c.tilt(35, -5)
    c.rotate(Rotation.PORTRAIT, gradual=False)
    c.set_microphone(True)
    assert c.microphone
    c.set_location(37.5, 127.0)
    until(lambda: fake.microphone and len(session.located) == 3)   # type: ignore[attr-defined]
    pulse = fake.sensors[ec.SensorValue.ACCELERATION].value.data
    assert [round(v, 2) for v in pulse] == [0.0, -25.81, 0.0]   # upside down: away from zero
    rots = [[round(v, 6) for v in p.value.data] for p in fake.physical]
    assert rots[0] == [0.0, 0.0, 0.0]                  # the shake ends by restoring the posture
    assert [r[2] for r in rots[1:7]] == [15.0, 30.0, 45.0, 60.0, 75.0, 90.0]   # 15° steps (§9.7)
    assert rots[7] == [20.0, 0.0, 85.0]                # tilt clamped to 20°, roll around the current z
    assert rots[8:] == [[0.0, 0.0, 0.0]]               # pre-rotation: one step
    assert session.located == [(37.5, 127.0, 30.0)] * 3   # type: ignore[attr-defined]


def fingers(e: ec.InputEvent) -> list[tuple[int, int, int, int]]:
    return [(t.identifier, t.x, t.y, t.pressure) for t in e.touch_event.touches]


def test_two_fingers_go_as_touches_and_always_lift(fake: FakeEmulatorServer, session: GrpcDeviceSession) -> None:
    """[P4] Ctrl+drag pinch: one TouchEvent per move, ids 0 and 1; pressure 0 lifts (the emulator keeps a finger
    down otherwise). The mouse finger lifts first; release_all and a new stream lift the touch fingers too."""
    a, b = PointerAt(0.25, 0.25, 0), PointerAt(0.75, 0.75, 0)
    session.input.pointer(PointerAt(0.5, 0.5, 0), True)
    session.input.touches((a, b))
    session.input.touches((PointerAt(0.2, 0.2, 0), PointerAt(0.8, 0.8, 0)))
    session.input.touches(())
    session.input.touches(())                  # nothing down: nothing to send
    session.input.touches((a, b))
    session.input.release_all()
    session.input.touches((a, b))
    session._input.restart_stream()            # in queue order, as a broken connection does
    until(lambda: len(fake.input_events) == 9)
    ev = fake.input_events
    assert [e.mouse_event.buttons for e in ev[:2]] == [1, 0]   # the mouse finger lifts before two go down
    assert fingers(ev[2]) == [(0, 180, 320, 512), (1, 540, 960, 512)]
    assert fingers(ev[3]) == [(0, 144, 256, 512), (1, 576, 1024, 512)]
    assert fingers(ev[4]) == [(0, 144, 256, 0), (1, 576, 1024, 0)]
    assert fingers(ev[5]) == fingers(ev[2]) and fingers(ev[6]) == [(0, 180, 320, 0), (1, 540, 960, 0)]
    assert fingers(ev[7]) == fingers(ev[2]) and fingers(ev[8]) == fingers(ev[6])   # the new stream lifts first


def test_controls_read_a_kept_phone_and_send_light_battery_text_and_call(fake: FakeEmulatorServer) -> None:
    """[P4] A kept phone comes back turned, lit and charged as it was saved (device check): the controls read it, so
    the next turn starts from where the phone is."""
    P, B = ec.PhysicalModelValue, ec.BatteryState   # noqa: N806
    fake.models.update({P.ROTATION: [0.0, 0.0, 90.0], P.LIGHT: [123.0], P.PROXIMITY: [0.0]})
    fake.battery = B(hasBattery=True, isPresent=True, charger=B.NONE, chargeLevel=15, status=B.DISCHARGING)
    s = GrpcDeviceSession(DeviceEndpoint(f"localhost:{fake.port}", fake.token, "emulator-5640", 1))
    try:
        c = s.controls
        assert c is not None
        assert c.posture == Rotation.LANDSCAPE and c.world == PhoneWorld(123.0, 0.0, 15, False)
        c.rotate(Rotation.PORTRAIT)
        c.set_light(400.0)
        c.set_proximity(1.0)
        c.set_battery(10, False)                       # unplugged: not below 21 % (SystemUI, 4a)
        assert c.world.battery == 21
        c.set_battery(130, True)                       # clamped
        c.receive_sms("01012345678", "안녕 Orcus")
        c.call_in("01087654321")
        c.call_end("01087654321")
        assert c.world == PhoneWorld(400.0, 1.0, 100, True)   # at once, like the posture
        until(lambda: [m for m, _ in fake.calls].count("sendPhone") == 2)
        turns = [round(p.value.data[2]) for p in fake.physical if p.target == P.ROTATION]
        assert turns == [75, 60, 45, 30, 15, 0]       # from the kept landscape, not an assumed portrait
        assert fake.models[P.LIGHT] == [400.0] and fake.models[P.PROXIMITY] == [1.0]
        assert all(p.interpolation == P.STEP for p in fake.physical)
        assert (fake.battery.chargeLevel, fake.battery.charger, fake.battery.status) == (100, B.AC, B.CHARGING)
        assert [a for m, a in fake.calls if m in ("sendSms", "sendPhone")] == [
            ("01012345678", "안녕 Orcus"), (ec.PhoneCall.InitCall, "01087654321"),
            (ec.PhoneCall.DisconnectCall, "01087654321")]
    finally:
        s.close()


def test_resend_repeats_the_newest_location_starting_from_home(fake: FakeEmulatorServer,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controls, "LOCATION_GAP_S", 0.02)
    located: list[tuple[float, float, float]] = []
    s = GrpcDeviceSession(DeviceEndpoint(f"localhost:{fake.port}", fake.token, "emulator-5640", 1),
                          location=lambda lat, lon, alt: located.append((lat, lon, alt)), home=(37.5665, 126.978))
    try:
        c = s.controls
        assert c is not None
        c.resend_location()                     # the start place SESSION_INIT set
        until(lambda: len(located) == 3)
        c.set_location(35.1798, 129.075)
        until(lambda: len(located) == 6)
        c.resend_location()
        until(lambda: len(located) == 9)
        assert located == [(37.5665, 126.978, 30.0)] * 3 + [(35.1798, 129.075, 30.0)] * 6
    finally:
        s.close()


def test_a_newer_location_replaces_the_one_being_repeated(session: GrpcDeviceSession,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controls, "LOCATION_GAP_S", 0.2)
    c = session.controls
    assert c is not None
    c.set_location(37.5, 127.0)
    until(lambda: len(session.located) == 1)   # type: ignore[attr-defined]
    c.set_location(35.1, 129.0)
    until(lambda: len(session.located) == 4)   # type: ignore[attr-defined]
    time.sleep(0.5)
    assert session.located == [(37.5, 127.0, 30.0)] + [(35.1, 129.0, 30.0)] * 3   # type: ignore[attr-defined]


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
