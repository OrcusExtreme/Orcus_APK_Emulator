"""gui without a device (offscreen Qt): shortcuts, 9:16 sizing, input mapping and finger release, frame pump,
and the main window's wiring (engine states, runs, problem channels, one handler per RemedyAction)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPointF, QSize, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent, QKeySequence, QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

from orcus.common import messages_ko
from orcus.contracts import (
    EngineState,
    EngineStatus,
    EpochCause,
    ErrorCode,
    Frame,
    HwKey,
    InputSettings,
    PointerAt,
    RemedyAction,
    Rotation,
    RunEvent,
    RunStage,
    SessionMode,
    Settings,
)
from orcus.gui import keymap, screen, window
from orcus.gui.bridge import Bridge
from orcus.gui.frame_pump import FramePump


@pytest.fixture(scope="module")
def qapp() -> Iterator[QApplication]:
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    yield app


class Sink:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def pointer(self, at: PointerAt, pressed: bool) -> None:
        self.calls.append(("pointer", round(at.u, 3), round(at.v, 3), pressed))

    def wheel(self, at: PointerAt, dy: int) -> None:
        self.calls.append(("wheel", round(at.u, 3), round(at.v, 3), dy))

    def key(self, key: object, *, long_press: bool = False) -> None:
        self.calls.append(("key", str(key), long_press))

    def chord(self, modifier: str, key: str) -> None:
        self.calls.append(("chord", modifier, key))

    def type_ascii(self, text: str) -> None:
        self.calls.append(("ascii", text))

    def paste_text(self, text: str) -> None:
        self.calls.append(("paste", text))

    def release_all(self) -> None:
        self.calls.append(("release_all",))

    def touches(self, points: Sequence[PointerAt]) -> None:
        self.calls.append(("touches", *((round(p.u, 3), round(p.v, 3)) for p in points)))


def mouse(kind: QEvent.Type, x: float, y: float, button: Qt.MouseButton,
          buttons: Qt.MouseButton = Qt.MouseButton.NoButton) -> QMouseEvent:
    p = QPointF(x, y)
    return QMouseEvent(kind, p, p, button, buttons, Qt.KeyboardModifier.NoModifier)


def test_shortcuts_are_unique_and_leave_the_phone_keys_alone() -> None:
    seqs = [QKeySequence(v).toString() for v in keymap.SHORTCUTS.values()]
    assert len(seqs) == len(set(seqs))
    assert not set(seqs) & {QKeySequence(k).toString() for k in keymap.PHONE_EDIT_KEYS}
    wanted = {keymap.SHORTCUTS[k] for k in ("shake", "rotate_left", "rotate_right", "hangul_bar")}
    assert wanted == {"F6", "F7", "F8", "Ctrl+T"}   # §9.5


def test_fit_gives_exact_9_by_16_like_the_design_example() -> None:
    chrome = QSize(68 + 360 + 48, 256)
    assert screen.fit(QSize(1920, 1032), chrome=chrome, landscape=False, dpr=1.0) == QSize(432, 768)
    assert screen.fit(QSize(1920, 1032), chrome=chrome, landscape=True, dpr=1.0) == QSize(1280, 720)
    big = screen.fit(QSize(3840, 2100), chrome=chrome, landscape=False, dpr=1.5)   # never above 720x1280 physical
    assert big.width() * 1.5 <= 720 and big.height() * 1.5 <= 1280 and big.width() * 16 == big.height() * 9


def test_screen_maps_mouse_and_always_lifts_the_finger(qapp: QApplication) -> None:
    v = screen.ScreenView(FramePump(Bridge()), lambda: InputSettings())
    v.resize(400, 800)
    sink = Sink()
    v.set_input(sink)
    v.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, 100, 200, Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton))
    v.mouseMoveEvent(mouse(QEvent.Type.MouseMove, 200, 400, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
    v.mouseMoveEvent(mouse(QEvent.Type.MouseMove, 450, 400, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
    v.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, 450, 400, Qt.MouseButton.LeftButton))
    assert sink.calls == [("pointer", 0.25, 0.25, True), ("pointer", 0.5, 0.5, True),
                          ("pointer", 1.0, 0.5, False)]   # left the screen: lifted at the edge, once
    sink.calls.clear()
    v.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, 40, 80, Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton))
    v.release()                                             # focus lost / window deactivated / rotation
    v.release()
    v.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, 1, 1, Qt.MouseButton.RightButton))
    v.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, 1, 1, Qt.MouseButton.MiddleButton))
    assert sink.calls == [("pointer", 0.1, 0.1, True), ("pointer", 0.1, 0.1, False),
                          ("key", str(HwKey.BACK), False), ("key", str(HwKey.HOME), False)]


def test_ctrl_drag_is_a_two_finger_pinch_mirrored_through_the_centre(qapp: QApplication) -> None:
    """[P4] The second finger mirrors the pointer through the screen centre; both lift on release, at the edge and
    on focus loss; the wheel waits while two fingers are down."""
    v = screen.ScreenView(FramePump(Bridge()), lambda: InputSettings())
    v.resize(400, 800)
    sink = Sink()
    v.set_input(sink)
    ctrl = Qt.KeyboardModifier.ControlModifier

    def press(x: float, y: float) -> None:
        p = QPointF(x, y)
        v.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, p, p, Qt.MouseButton.LeftButton,
                                      Qt.MouseButton.LeftButton, ctrl))
    press(160, 320)
    v.mouseMoveEvent(mouse(QEvent.Type.MouseMove, 100, 200, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
    v.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, 100, 200, Qt.MouseButton.LeftButton))
    assert sink.calls == [("touches", (0.4, 0.4), (0.6, 0.6)), ("touches", (0.25, 0.25), (0.75, 0.75)),
                          ("touches",)]
    sink.calls.clear()
    press(100, 200)
    v.mouseMoveEvent(mouse(QEvent.Type.MouseMove, -5, 200, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
    v.mouseMoveEvent(mouse(QEvent.Type.MouseMove, 50, 200, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
    press(100, 200)
    v.release()                                             # focus lost / rotation
    v.release()
    assert sink.calls == [("touches", (0.25, 0.25), (0.75, 0.75)), ("touches",),   # left the screen: lifted
                          ("touches", (0.25, 0.25), (0.75, 0.75)), ("touches",)]


def test_a_vibration_shows_a_pill_for_its_length(qapp: QApplication) -> None:
    v = screen.ScreenView(FramePump(Bridge()), lambda: InputSettings())
    v.resize(400, 800)
    v.buzz(-1)
    assert not v.buzzing.isHidden() and v.buzzing.text() == "진동"
    assert v._buzz_timer.interval() == screen.BUZZ_MIN_MS   # noqa: SLF001
    v.buzz(3000)
    assert v.buzzing.text() == "진동 3.0초" and v._buzz_timer.interval() == 3000   # noqa: SLF001
    v.buzz(60000)
    assert v._buzz_timer.interval() == screen.BUZZ_MAX_MS   # noqa: SLF001
    v.clear()                                               # the phone went away
    assert v.buzzing.isHidden()


def test_screen_keys(qapp: QApplication) -> None:
    v = screen.ScreenView(FramePump(Bridge()), lambda: InputSettings())
    sink = Sink()
    v.set_input(sink)

    def press(k: Qt.Key, text: str = "", mods: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier) -> None:
        v.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, k, mods, text))
    ctrl = Qt.KeyboardModifier.ControlModifier
    press(Qt.Key.Key_Escape)
    press(Qt.Key.Key_A, "a")
    press(Qt.Key.Key_A, "\x01", ctrl)
    press(Qt.Key.Key_Backspace)
    press(Qt.Key.Key_Left, "", ctrl)
    press(Qt.Key.Key_Tab, "\t")
    assert sink.calls == [("key", str(HwKey.BACK), False), ("ascii", "a"), ("chord", "Control", "a"),
                          ("key", "Backspace", False), ("chord", "Control", "ArrowLeft"), ("key", "Tab", False)]
    QGuiApplication.clipboard().setText("안녕하세요")
    if QGuiApplication.clipboard().text() == "안녕하세요":   # the offscreen platform may lack a clipboard
        sink.calls.clear()
        press(Qt.Key.Key_V, "\x16", ctrl)
        assert sink.calls == [("paste", "안녕하세요")]
    v.set_input(None)
    press(Qt.Key.Key_A, "a")   # no phone: nothing breaks


def test_frame_pump_keeps_the_newest_and_signals_once_per_paint(qapp: QApplication) -> None:
    b = Bridge()
    hits: list[int] = []
    b.frame.connect(lambda: hits.append(1))
    p = FramePump(b)
    f = Frame(bytes(9 * 16 * 3), 9, 16, Rotation.PORTRAIT, 1, int(time.time() * 1e6))
    p.on_frame(f)
    p.on_frame(replace(f, seq=3))
    assert hits == [1] and p.dropped == 1 and p.gaps == 1
    shown = p.take()
    assert shown is not None and shown.seq == 3 and shown.image.width() == 9
    p.on_frame(replace(f, seq=4))
    assert hits == [1, 1]


# --- main window with a fake session -------------------------------------------------------------------------------
class FakeFrames:
    def __init__(self) -> None:
        self.started: list[tuple[int, int]] = []
        self.stopped = 0
        self.retargets: list[tuple[int, int]] = []

    def start(self, width: int, height: int, on_frame: Callable[[Frame], None], on_end: object) -> None:
        self.started.append((width, height))

    def retarget(self, width: int, height: int) -> None:
        self.retargets.append((width, height))

    def stop(self) -> None:
        self.stopped += 1


class FakeDevice:
    def __init__(self) -> None:
        self.epoch = 1
        self.frames = FakeFrames()
        self.input = Sink()
        self.controls = None

    def logcat(self) -> object: ...
    def screenshot_png(self, on_done: Callable[[bytes | None], None]) -> None: ...
    def close(self) -> None: ...


class FakeEngine:
    def __init__(self) -> None:
        self.st = EngineStatus(EngineState.BOOTING, "engine.state.booting", 0.3, SessionMode.GOLDEN, 0,
                               EpochCause.BOOT, True, (), None, None)
        self.retried = 0
        self.resets = 0

    def status(self) -> EngineStatus:
        return self.st

    def start(self) -> None: ...
    def stop(self, *, fast: bool = False) -> None: ...

    def reset_device(self) -> int:
        self.resets += 1
        return self.st.epoch + 1

    def rebuild(self, reason: str) -> None: ...

    def keep_once(self) -> None:
        self.kept = getattr(self, "kept", 0) + 1

    def retry_gpu(self) -> None:
        self.gpu_retried = getattr(self, "gpu_retried", 0) + 1

    def retry(self) -> None:
        self.retried += 1

    def subscribe(self, cb: object) -> Callable[[], None]:
        return lambda: None


class FakeApps:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Callable[..., object]:
        return lambda *a, **k: self.calls.append(name)


class FakeSessionApi:
    def __init__(self) -> None:
        self.engine = FakeEngine()
        self.apps = FakeApps()
        self.dev = FakeDevice()
        self.opened: list[list[Path]] = []

    def device(self) -> FakeDevice | None:
        return self.dev if self.engine.st.state == EngineState.READY else None

    def settings(self) -> Settings:
        return Settings()

    def report(self) -> str:
        return "report"

    def open_paths(self, paths: list[Path]) -> None:
        self.opened.append(paths)


def test_window_wiring(qapp: QApplication, tmp_path: Path) -> None:
    s = FakeSessionApi()
    w = window.MainWindow(s, Bridge(), tmp_path)   # type: ignore[arg-type]
    assert set(window.REMEDIES) == set(RemedyAction)
    assert all(callable(getattr(w, m, None)) for m in window.REMEDIES.values())
    assert w.screen_view.size() == QSize(432, 768) or w.screen_view.width() * 16 == w.screen_view.height() * 9
    w.on_engine(s.engine.st)
    assert not w.overlay.isHidden() and s.dev.frames.started == []
    s.engine.st = replace(s.engine.st, state=EngineState.READY, text_key="engine.state.ready", epoch=1,
                          progress=None)
    w.on_engine(s.engine.st)
    assert len(s.dev.frames.started) == 1 and w.overlay.isHidden()
    ev = RunEvent("r1", RunStage.RUNNING, None, (), None, None, 4.8, 4321, 1)
    w.on_run(ev)
    assert "실행 중" in w.status_app.text() and "4.8" in w.status_app.text()
    w.show_problem(messages_ko.error(ErrorCode.APP_AI_COMPONENT_ERROR, "601 · ActivityStarter.StartActivity: x",
                                     message_key="app.error.601"))
    assert w.log_panel.count == 1 and "로그 (1)" == w.tabs.tabText(w.tabs.indexOf(w.log_panel))
    assert [w.tabs.tabText(i) for i in range(2)] == ["앱", "센서"]
    w.remedy(RemedyAction.RUN_AGAIN)
    w.remedy(RemedyAction.GRANT_ALL)
    assert s.apps.calls == ["rerun", "grant_all"]
    failed = messages_ko.error(ErrorCode.ENGINE_BOOT_TIMEOUT, "boot", "line 1\nline 2")
    s.engine.st = replace(s.engine.st, state=EngineState.FAILED, error=failed, text_key="engine.state.failed")
    w.on_engine(s.engine.st)
    assert not w.engine_panel.isHidden() and s.dev.frames.stopped == 1
    w.remedy(RemedyAction.RETRY)
    assert s.engine.retried == 1
    s.engine.st = replace(s.engine.st, state=EngineState.READY, error=None, warnings=(ErrorCode.ENGINE_GPU_SOFTWARE,),
                          epoch=2, epoch_cause=EpochCause.CRASH_RESTART)
    w.on_engine(s.engine.st)
    assert list(w.banner_widgets) == [str(ErrorCode.ENGINE_GPU_SOFTWARE)] and len(s.dev.frames.started) == 2
    assert w.toast.text() == window.CRASH_NOTICE[SessionMode.GOLDEN]   # B11: golden data went back to the start
    w.quiet_close = True   # an app ran in GOLDEN: the close notice is test_gui_3b's
    w.close()


def test_untrusted_app_text_is_never_rich_text(qapp: QApplication) -> None:
    """A manifest label like `<img src=//host/x.png>` loaded over SMB as rich text (3a review)."""
    from orcus.gui import overlays, panels
    p = panels.AppPanel()
    host = QWidget()   # keep it: a collected parent deletes the overlay with it
    o = overlays.Overlay(host)
    b = overlays.Banner("<b>crash</b>", red=True)
    labels = [p.title, p.facts, o.label, *b.findChildren(type(p.title))]
    assert all(label.textFormat() is Qt.TextFormat.PlainText for label in labels)


def test_only_existing_local_files_are_handed_over(tmp_path: Path) -> None:
    """Another account could hand over a share path and make us fetch it with our credentials (3a review)."""
    from orcus.gui import instance
    apk = tmp_path / "고양이.apk"
    apk.write_bytes(b"x")
    assert instance.acceptable(str(apk))
    for bad in (r"\\host\share\x.apk", "//host/share/x.apk", "relative.apk", str(tmp_path / "missing.apk"), ""):
        assert not instance.acceptable(bad), bad


def test_app_icon_is_loaded_and_set_on_window(qapp: QApplication) -> None:
    '''Orcus logo icon is resolved and has embedded multi-resolution sizes.'''
    from orcus.gui import app as gui_app
    icon = gui_app.app_icon()
    assert not icon.isNull()
    sizes = [sz.width() for sz in icon.availableSizes()]
    assert 16 in sizes and 32 in sizes and 256 in sizes
