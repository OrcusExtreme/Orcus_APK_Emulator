"""Phase 3b GUI without a device (offscreen Qt): the [Sensors] tab and side buttons driving DeviceControls, the
Korean input bar, the settings dialog, run warnings as banners, the close notice, wheel and 한/영 key, the side
key dragged off, and the single-instance hand-over (pipe owner, a closing first instance)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QInputMethodEvent, QKeyEvent, QWheelEvent
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from orcus.common import config, win32
from orcus.contracts import (
    ApkInfo,
    ApplyResult,
    EngineState,
    ErrorCode,
    Frame,
    HwKey,
    InputSettings,
    PhoneWorld,
    Rotation,
    RunEvent,
    RunStage,
    SessionMode,
    Sensitivity,
    Settings,
)
from orcus.gui import instance, screen, window
from orcus.gui.bridge import Bridge
from orcus.gui.frame_pump import FramePump
from orcus.gui.hangul import TOOLTIP, HangulBar
from orcus.gui.phone import KeyButton
from orcus.gui.settings_dialog import SettingsDialog
from test_gui import FakeSessionApi, Sink, mouse, qapp   # noqa: F401  (qapp is a fixture)

REC = "android.permission.RECORD_AUDIO"


class Controls:
    def __init__(self) -> None:
        self.posture = Rotation.PORTRAIT
        self.microphone = False
        self.world = PhoneWorld()
        self.calls: list[tuple[object, ...]] = []

    def shake(self, sensitivity: Sensitivity) -> None:
        self.calls.append(("shake", sensitivity))

    def rotate(self, to: Rotation, *, gradual: bool = True) -> None:
        self.posture = to
        self.calls.append(("rotate", to, gradual))

    def tilt(self, pitch_deg: float, roll_deg: float) -> None:
        self.calls.append(("tilt", pitch_deg, roll_deg))

    def set_location(self, lat: float, lon: float, alt_m: float = 30.0) -> None:
        self.calls.append(("location", round(lat, 4), round(lon, 4)))

    def resend_location(self) -> None:
        self.calls.append(("resend",))

    def set_microphone(self, enabled: bool) -> None:
        self.microphone = enabled
        self.calls.append(("mic", enabled))

    def set_light(self, lux: float) -> None:
        self.calls.append(("light", lux))

    def set_proximity(self, cm: float) -> None:
        self.calls.append(("proximity", cm))

    def set_battery(self, level: int, charging: bool) -> None:
        self.calls.append(("battery", level, charging))

    def receive_sms(self, sender: str, text: str) -> None:
        self.calls.append(("sms", sender, text))

    def call_in(self, number: str) -> None:
        self.calls.append(("call_in", number))

    def call_end(self, number: str) -> None:
        self.calls.append(("call_end", number))

    def of(self, kind: str) -> list[tuple[object, ...]]:
        return [c for c in self.calls if c[0] == kind]


class Api(FakeSessionApi):
    """FakeSessionApi with controls and settings that really change."""

    def __init__(self, mode: SessionMode = SessionMode.GOLDEN) -> None:
        super().__init__()
        self.controls = Controls()
        self.dev.controls = self.controls   # type: ignore[assignment]
        self.current = Settings()
        self.saved: list[dict[str, object]] = []
        self.rebuilt: list[str] = []
        self.engine.rebuild = self.rebuilt.append   # type: ignore[method-assign]
        self.engine.st = replace(self.engine.st, mode=mode)

    def settings(self) -> Settings:
        return self.current

    def preview_settings(self, changes: Mapping[str, object]) -> ApplyResult:
        return config.classify(self.current, config.apply(self.current, changes))

    def update_settings(self, changes: Mapping[str, object]) -> ApplyResult:
        new = config.apply(self.current, changes)
        result = config.classify(self.current, new)
        self.current = new
        self.saved.append(dict(changes))
        return result


def info(package: str, *permissions: str) -> ApkInfo:
    return ApkInfo("0" * 64, Path("x.apk"), package, ".Screen1", package.rsplit(".", 1)[-1], None, 1, "1.0", 21, 34,
                   frozenset(permissions), frozenset(), frozenset(), frozenset(), False, True, False, "2024", None)


def run(stage: RunStage, app: ApkInfo | None, warnings: tuple[ErrorCode, ...] = ()) -> RunEvent:
    return RunEvent("r1", stage, app, (), None, None, 2.5, 4321, 1, warnings)


def ready(s: Api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, mic: bool = True) -> window.MainWindow:
    monkeypatch.setattr(window, "host_has_mic", lambda: mic)
    w = window.MainWindow(s, Bridge(), tmp_path / "logs")   # type: ignore[arg-type]
    w.quiet_close = True   # close() in a test never asks; the close-notice test calls _may_close() itself
    s.engine.st = replace(s.engine.st, state=EngineState.READY, text_key="engine.state.ready", epoch=1, progress=None)
    w.on_engine(s.engine.st)
    return w


def test_sensor_tab_and_keys_drive_the_controls(qapp: QApplication, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    s = Api()
    w = ready(s, tmp_path, monkeypatch)
    c = s.controls
    assert w.sensors.shake_button.isEnabled() and w.sensors.pad.isEnabled()
    w.rotate(1)                       # F7: left
    w.rotate(1)                       # again, from the newest posture (not the frame, which lags)
    w.rotate(-1)                      # F8
    assert c.of("rotate") == [("rotate", Rotation.LANDSCAPE, True), ("rotate", Rotation.REVERSE_PORTRAIT, True),
                              ("rotate", Rotation.LANDSCAPE, True)]
    w.shake()
    w.shake()                         # F6 held: the key repeat comes 30 times a second
    assert c.of("shake") == [("shake", Sensitivity.MODERATE)]
    w._last_shake -= 0.5              # noqa: SLF001  (450 ms later)
    w.shake()
    assert len(c.of("shake")) == 2
    w.sensors.pad.moved.emit(10.0, -5.0)
    assert c.of("tilt") == [("tilt", 10.0, -5.0)] and "10" in w.sensors.tilt_label.text()
    w.sensors.preset_box.activated.emit(0)
    assert c.of("location") == [("location", 37.5665, 126.978)]
    w._phone_action("shake")          # noqa: SLF001  (side buttons go the same way)
    w._phone_action("rotate_right")   # noqa: SLF001
    assert c.posture == Rotation.PORTRAIT
    w.close()


def test_the_window_turns_with_the_frames(qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """B5: a landscape frame turns the screen and the bezel (16:9) and the [Sensors] tab says so."""
    w = ready(Api(), tmp_path, monkeypatch)
    portrait = w.screen_view.size()
    assert portrait.width() * 16 == portrait.height() * 9
    for rotation, size in ((Rotation.LANDSCAPE, (64, 36)), (Rotation.PORTRAIT, (36, 64))):
        w.pump.on_frame(Frame(bytes(size[0] * size[1] * 3), size[0], size[1], rotation, 1, 0))
        w._on_frame()                                       # noqa: SLF001  (bridge.frame)
        shown = w.screen_view.size()
        if rotation is Rotation.LANDSCAPE:          # refitted to the monitor as 16:9, not just turned
            assert shown.width() * 9 == shown.height() * 16
        else:
            assert shown == portrait
        assert ("가로" in w.sensors.posture_label.text()) is (rotation is Rotation.LANDSCAPE)
    w.close()


def test_sensitivity_and_microphone_are_remembered_per_app(qapp: QApplication, tmp_path: Path,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    s = Api()
    w = ready(s, tmp_path, monkeypatch)
    rec, plain = info("appinventor.ai_test.Rec", REC), info("appinventor.ai_test.Plain")
    w.on_run(run(RunStage.RUNNING, rec))
    assert "마이크" in w.toast.text()                       # B13: the app records, the microphone is off: say so
    w.sensors.sensitivity_box.setCurrentIndex(2)
    w.sensors.sensitivity_box.activated.emit(2)
    w.sensors.mic_button.setChecked(True)
    assert s.saved == [{"sensors.shake_sensitivity": {"appinventor.ai_test.Rec": "strong"}},
                       {"privacy.mic_allowed_packages": ["appinventor.ai_test.Rec"]}]
    assert s.controls.of("mic") == [("mic", True)] and w.status_mic.text() == "마이크 켜짐"
    w.on_run(run(RunStage.RUNNING, plain))
    assert w.sensors.sensitivity() is Sensitivity.MODERATE   # the other app: the default
    w.on_run(run(RunStage.RUNNING, rec))
    assert w.sensors.sensitivity() is Sensitivity.STRONG
    w.sensors.mic_button.setChecked(False)
    assert s.saved[-1] == {"privacy.mic_allowed_packages": []} and w.status_mic.text() == "마이크 꺼짐"
    w.close()


def test_a_pc_without_a_microphone_disables_the_button(qapp: QApplication, tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    w = ready(Api(), tmp_path, monkeypatch, mic=False)
    assert not w.sensors.mic_button.isEnabled() and "마이크가 없습니다" in w.sensors.mic_note.text()
    assert w.status_mic.text() == "마이크 없음"
    w.close()


def test_run_warnings_are_banners_with_their_remedy(qapp: QApplication, tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    s = Api()
    w = ready(s, tmp_path, monkeypatch)
    key = str(ErrorCode.APP_ORIENTATION_MISMATCH)
    w.on_run(run(RunStage.RUNNING, info("appinventor.ai_test.A"), (ErrorCode.APP_ORIENTATION_MISMATCH,)))
    assert key in w.banner_widgets
    w.on_engine(s.engine.st)                                 # an engine update keeps the app's banner
    assert key in w.banner_widgets
    button = w.banner_widgets[key].findChildren(QPushButton)[0]
    assert button.text() == "화면 방향 맞추기"
    button.click()
    assert s.apps.calls == ["match_orientation"]
    w.on_run(run(RunStage.RUNNING, info("appinventor.ai_test.A")))
    assert key not in w.banner_widgets
    w.close()


@pytest.mark.parametrize(("mode", "used", "asked"), [
    (SessionMode.GOLDEN, True, True), (SessionMode.GOLDEN, False, False), (SessionMode.COLD, True, False),
])
def test_close_notice_only_in_golden_after_an_app(qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                  mode: SessionMode, used: bool, asked: bool) -> None:
    """B14: the GOLDEN phone forgets the app's data at close; COLD keeps it and does not ask."""
    s = Api(mode)
    w = ready(s, tmp_path, monkeypatch)
    shown: list[str] = []
    answer = ["취소"]

    def fake_exec(box: QMessageBox) -> int:
        shown.append(box.text())
        box.checkBox().setChecked(True)
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: next(b for b in box.buttons()
                                                                        if b.text() == answer[0]))
    if used:
        w.on_run(run(RunStage.RUNNING, info("appinventor.ai_test.A")))
    assert w._may_close() is not asked                      # noqa: SLF001  ([취소] keeps the window)
    assert shown == ([window.CLOSE_NOTICE] if asked else [])
    if asked:
        answer[0] = "닫기"
        assert w._may_close()                               # noqa: SLF001
        assert s.saved == [{"ui.confirm_close_data": False}]   # 다시 묻지 않기
        assert w._may_close() and len(shown) == 2           # noqa: SLF001  (not asked again)
    w.quiet_close = True                                     # logoff: never asks
    w.close()
    assert len(shown) == (2 if asked else 0)


def test_hangul_bar_sends_on_enter_keeps_lines_and_closes_on_esc(qapp: QApplication) -> None:
    bar = HangulBar()
    sent: list[str] = []
    closed: list[bool] = []
    bar.send.connect(sent.append)
    bar.closed.connect(lambda: closed.append(True))
    bar.open_bar()
    assert bar.isVisible() and bar.edit.toolTip() == TOOLTIP

    def key(k: Qt.Key, mods: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier, text: str = "") -> None:
        bar.edit.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, k, mods, text))
    bar.edit.insertPlainText("안녕하세요")
    key(Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier, "\r")   # a new line (MultiLine keeps it)
    bar.edit.insertPlainText("Orcus")
    key(Qt.Key.Key_Return, text="\r")
    assert sent == ["안녕하세요\nOrcus"] and bar.edit.toPlainText() == "" and bar.isVisible()
    key(Qt.Key.Key_Return, text="\r")                        # nothing typed: nothing sent
    key(Qt.Key.Key_Escape)
    assert sent == ["안녕하세요\nOrcus"] and closed == [True] and not bar.isVisible()


def test_window_sends_the_bar_text_and_toggles_it(qapp: QApplication, tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    s = Api()
    w = ready(s, tmp_path, monkeypatch)
    w.show()
    w.toggle_hangul()
    assert w.hangul_bar.isVisible()
    w.hangul_bar.send.emit("안녕하세요 Orcus")
    assert ("paste", "안녕하세요 Orcus") in s.dev.input.calls
    w.toggle_hangul()
    assert not w.hangul_bar.isVisible()
    w.quiet_close = True
    w.close()


def test_settings_dialog_saves_only_changes_and_confirms_the_gpu(qapp: QApplication, tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    s = Api()
    d = SettingsDialog(s, tmp_path, lambda: None)            # type: ignore[arg-type]
    assert d.changes() == {}
    d.grant_all.setChecked(True)
    d.sensitivity.setCurrentIndex(2)
    gpu = d.gpu.button(2)
    assert gpu is not None
    gpu.setChecked(True)
    answers = [QMessageBox.StandardButton.No]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: answers[0])
    d._apply()                                               # noqa: SLF001  (declined: nothing saved)
    assert s.saved == [] and s.rebuilt == []
    answers[0] = QMessageBox.StandardButton.Yes
    d._apply()                                               # noqa: SLF001
    assert s.saved == [{"sensors.shake_default": "strong", "apk.grant_all": True, "engine.gpu_mode": "software"}]
    assert s.rebuilt == ["GPU 모드를 software(으)로 바꿈"]
    d2 = SettingsDialog(s, tmp_path, lambda: None)           # type: ignore[arg-type]
    d2.esc_back.setChecked(False)
    d2.ask_close.setChecked(False)
    answers[0] = QMessageBox.StandardButton.No               # nothing to confirm without a GPU change
    d2._apply()                                              # noqa: SLF001
    assert s.saved[-1] == {"input.esc_is_back": False, "ui.confirm_close_data": False} and len(s.rebuilt) == 1


def test_screen_wheel_scrolls(qapp: QApplication) -> None:
    v = screen.ScreenView(FramePump(Bridge()), lambda: InputSettings())
    v.resize(400, 800)
    sink = Sink()
    v.set_input(sink)
    p = QPointF(200, 200)
    v.wheelEvent(QWheelEvent(p, p, QPoint(0, 0), QPoint(0, -120), Qt.MouseButton.NoButton,
                             Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False))
    assert sink.calls == [("wheel", 0.5, 0.25, -120)]


def ime(view: screen.ScreenView, preedit: str, commit: str = "") -> None:
    """What the Windows IME sends through Qt while composing Korean (QWindowsInputContext)."""
    ev = QInputMethodEvent(preedit, [])
    if commit:
        ev.setCommitString(commit)
    view.inputMethodEvent(ev)


def test_korean_typed_on_the_screen_goes_to_the_phone_syllable_by_syllable(qapp: QApplication) -> None:
    """The user's 3b request: 한/영 on the phone screen and type. Finished syllables are pasted in order; the one
    being composed shows in the bubble only (the phone cannot show half a syllable)."""
    v = screen.ScreenView(FramePump(Bridge()), lambda: InputSettings())
    v.resize(400, 800)
    v.show()
    sink = Sink()
    v.set_input(sink)
    assert v.testAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)
    assert v.inputMethodQuery(Qt.InputMethodQuery.ImEnabled) is True
    for pre in ("ㅇ", "아", "안"):
        ime(v, pre)
    assert sink.calls == [] and v.composing.isVisible() and v.composing.text() == "안"
    ime(v, "ㄴ", commit="안")                       # the next consonant finishes 안
    ime(v, "녀")
    ime(v, "녕")
    ime(v, "", commit="녕")                         # space or Enter finishes the last one
    assert sink.calls == [("paste", "안"), ("paste", "녕")]
    assert not v.composing.isVisible()
    v.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " "))
    assert sink.calls[-1] == ("ascii", " ")         # after the syllables, in the same queue
    ime(v, "ㅎ")
    box = v.inputMethodQuery(Qt.InputMethodQuery.ImCursorRectangle)
    assert v.rect().contains(box) and box == v.composing.geometry()   # the 한자 window opens at the bubble
    v.set_input(None)
    ime(v, "", commit="하")                          # no phone: nothing breaks, nothing sent
    assert sink.calls[-1] == ("ascii", " ")
    v.close()


def test_side_key_dragged_off_is_not_pressed(qapp: QApplication) -> None:
    """3a review: releasing outside the button still sent the key."""
    b = KeyButton(HwKey.BACK, "back", "뒤로")
    b.resize(30, 30)
    got: list[object] = []
    b.pressed_key.connect(lambda k, _long: got.append(k))
    left = Qt.MouseButton.LeftButton
    QApplication.sendEvent(b, mouse(QEvent.Type.MouseButtonPress, 10, 10, left, left))
    QApplication.sendEvent(b, mouse(QEvent.Type.MouseMove, 200, 200, Qt.MouseButton.NoButton, left))
    QApplication.sendEvent(b, mouse(QEvent.Type.MouseButtonRelease, 200, 200, left))
    assert got == []
    QApplication.sendEvent(b, mouse(QEvent.Type.MouseButtonPress, 10, 10, left, left))
    QApplication.sendEvent(b, mouse(QEvent.Type.MouseButtonRelease, 10, 10, left))
    assert got == [HwKey.BACK]


def test_hand_over_goes_only_to_our_own_account(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    name = f"OrcusAPK.test.{os.getpid()}"
    monkeypatch.setattr(instance, "server_name", lambda: name)
    server = instance.InstanceServer(lambda paths: None)
    sock = QLocalSocket()
    sock.connectToServer(name)
    assert sock.waitForConnected(2000)
    assert win32.pipe_server_sid(int(sock.socketDescriptor())) == win32.user_sid() != ""
    sock.abort()
    monkeypatch.setattr(win32, "pipe_server_sid", lambda handle: "S-1-5-21-1-2-3-1001")   # someone else's pipe
    t = time.monotonic()
    assert instance.hand_over(["x.apk"], timeout_s=2) is False and time.monotonic() - t < 1.5
    server.close()


def test_a_second_start_waits_for_a_closing_first_instance() -> None:
    name = f"Local\\OrcusAPK.test.{os.getpid()}"
    handle, existed = win32.named_mutex(name)
    assert handle and not existed
    threading.Timer(0.3, lambda: win32.close_handle(handle)).start()
    t = time.monotonic()
    assert instance.wait_until_gone(name, 3.0) and time.monotonic() - t < 2.0
    handle, _ = win32.named_mutex(name)
    try:
        assert not instance.wait_until_gone(name, 0.3)        # still running: give up after the wait
    finally:
        win32.close_handle(handle)


def test_keep_this_time_at_the_close_notice(qapp: QApplication, tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """[P4] §6.1: the GOLDEN close notice offers to keep the phone this once; the engine saves at the stop."""
    s = Api()
    w = ready(s, tmp_path, monkeypatch)
    w.on_run(run(RunStage.RUNNING, info("appinventor.ai_test.A")))
    monkeypatch.setattr(QMessageBox, "exec", lambda box: 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: next(b for b in box.buttons()
                                                                        if b.text() == window.KEEP_ONCE))
    assert w._may_close()                                   # noqa: SLF001
    assert getattr(s.engine, "kept", 0) == 1
    w.close()


def test_settings_turn_keeping_on(qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = Api()
    d = SettingsDialog(s, tmp_path, lambda: None)            # type: ignore[arg-type]
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text: told.append(text))
    d.keep_state.setChecked(True)
    d._apply()                                               # noqa: SLF001
    assert s.saved == [{"session.keep_state": True}] and s.current.session.keep_state
    assert told and "보관" in told[0]                         # applies from the next close (needs_restart)


def test_settings_switch_to_the_ladder_restarts_instead_of_rebuilding(qapp: QApplication, tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """[P4] §5.6: the ladder starts at host on the same golden, so no 5-minute rebuild question."""
    s = Api()
    retried: list[bool] = []
    s.engine.retry_gpu = lambda: retried.append(True)        # type: ignore[method-assign]
    asked: list[object] = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.No)
    d = SettingsDialog(s, tmp_path, lambda: None)            # type: ignore[arg-type]
    ladder = d.gpu.button(3)
    assert ladder is not None and "자동" in ladder.text()
    ladder.setChecked(True)
    d._apply()                                               # noqa: SLF001
    assert asked == [] and retried == [True] and s.rebuilt == []
    assert s.saved == [{"engine.gpu_mode": "ladder"}]
