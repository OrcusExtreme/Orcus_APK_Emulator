"""Minimal PySide6 viewer (throwaway spike, Phase 2): frames, mouse -> touch, keys, Korean input bar.

For hands-on S2/S7/S8 checks. Boots the AVD cold through the spike harness, shows the screen 1:1 at a
9:16 size that fits the window's monitor (about 432x768 on a 1920x1080 primary), streams mouse drags as
one finger, sends Back/Home/Recents as keypresses, and pastes Korean text either with the Paste key or
with Ctrl+V so S8 can compare them. The status line shows fps and the press-to-frame delay.

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/viewer.py [host|host_hidden|software]
"""

import queue
import sys
import threading
import time

from harness import Live, LaunchMode
from PySide6.QtCore import QObject, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QImage, QKeyEvent, QMouseEvent, QPainter
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QVBoxLayout,
                               QWidget)

from orcus.contracts import GpuMode
from orcus.emugrpc.generated import emulator_controller_pb2 as ec

PANEL_W, PANEL_H = 720, 1280


class Bridge(QObject):
    frame = Signal()
    status = Signal(str)


class Session:
    """Owns the Live emulator, the frame stream thread and the input stream thread."""

    def __init__(self, gpu: GpuMode, bridge: Bridge, size: tuple[int, int]) -> None:
        self.gpu, self.bridge, self.size = gpu, bridge, size
        self.live: Live | None = None
        self.mailbox: QImage | None = None
        self.lock = threading.Lock()
        self.inputs: "queue.Queue[ec.InputEvent | None]" = queue.Queue()
        self.frames = 0
        self.pressed_at = 0.0
        self.last_delay_ms: float | None = None
        self.stream = None

    def start(self) -> None:
        threading.Thread(target=self._run, name="viewer-session", daemon=True).start()

    def _run(self) -> None:
        self.bridge.status.emit(f"휴대폰 켜는 중… ({self.gpu})")
        try:
            self.live = Live(mode=LaunchMode.COLD, gpu=self.gpu)
            self.live.start()
        except Exception as e:
            self.bridge.status.emit(f"부팅 실패: {e}")
            return
        self.bridge.status.emit(f"준비됨 · 부팅 {self.live.timings.get('booted')} s")
        threading.Thread(target=self._input_loop, name="viewer-input", daemon=True).start()
        self._frame_loop()

    def _frame_loop(self) -> None:
        assert self.live is not None and self.live.stub is not None
        w, h = self.size
        self.stream = self.live.stub.streamScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888, width=w, height=h))
        try:
            for img in self.stream:
                fw, fh = img.format.width, img.format.height   # Image.width/height are 0 in 37.2.12 (S2)
                if len(img.image) != fw * fh * 3 or fw == 0:
                    continue
                qimg = QImage(img.image, fw, fh, fw * 3, QImage.Format.Format_RGB888).copy()
                with self.lock:
                    pending = self.mailbox is not None
                    self.mailbox = qimg
                self.frames += 1
                if self.pressed_at and self.last_delay_ms is None:
                    self.last_delay_ms = 1000 * (time.monotonic() - self.pressed_at)
                if not pending:
                    self.bridge.frame.emit()
        except Exception as e:   # cancelled on close
            self.bridge.status.emit(f"화면 스트림 끝: {type(e).__name__}")

    def _input_loop(self) -> None:
        assert self.live is not None and self.live.stub is not None

        def events():
            while (event := self.inputs.get()) is not None:
                yield event
        self.live.stub.streamInputEvent(events())

    def take_frame(self) -> QImage | None:
        with self.lock:
            img, self.mailbox = self.mailbox, None
            return img

    def pointer(self, u: float, v: float, pressed: bool) -> None:
        x = min(PANEL_W - 1, max(0, round(u * PANEL_W)))
        y = min(PANEL_H - 1, max(0, round(v * PANEL_H)))
        if pressed and not self.pressed_at:
            self.pressed_at, self.last_delay_ms = time.monotonic(), None
        if not pressed:
            self.pressed_at = 0.0
        self.inputs.put(ec.InputEvent(mouse_event=ec.MouseEvent(x=x, y=y, buttons=1 if pressed else 0)))

    def key(self, name: str) -> None:
        self.inputs.put(ec.InputEvent(key_event=ec.KeyboardEvent(eventType=ec.KeyboardEvent.keypress, key=name)))

    def chord(self, modifier: str, key: str) -> None:
        for name, kind in ((modifier, ec.KeyboardEvent.keydown), (key, ec.KeyboardEvent.keypress),
                           (modifier, ec.KeyboardEvent.keyup)):
            self.inputs.put(ec.InputEvent(key_event=ec.KeyboardEvent(eventType=kind, key=name)))

    def paste(self, text: str, use_ctrl_v: bool) -> None:
        if self.live is None or self.live.stub is None:
            return
        self.live.stub.setClipboard(ec.ClipData(text=text), timeout=5)
        time.sleep(0.1)
        if use_ctrl_v:
            self.chord("Control", "v")
        else:
            self.key("Paste")

    def close(self) -> None:
        self.inputs.put(ec.InputEvent(mouse_event=ec.MouseEvent(x=0, y=0, buttons=0)))   # release the finger
        self.inputs.put(None)
        if self.stream is not None:
            self.stream.cancel()
        if self.live is not None:
            self.live.stop()


class PhoneView(QWidget):
    def __init__(self, session: Session, size: tuple[int, int]) -> None:
        super().__init__()
        self.session = session
        self.image: QImage | None = None
        self.setFixedSize(*size)
        self.setMouseTracking(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def show_latest(self) -> None:
        img = self.session.take_frame()
        if img is not None:
            self.image = img
            self.update()

    def paintEvent(self, event: object) -> None:   # 1:1 blit, no scaling
        p = QPainter(self)
        if self.image is not None:
            p.drawImage(0, 0, self.image)
        else:
            p.fillRect(self.rect(), Qt.GlobalColor.black)
        p.end()

    def _uv(self, pos: QPointF) -> tuple[float, float]:
        return pos.x() / self.width(), pos.y() / self.height()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.session.pointer(*self._uv(e.position()), True)
        elif e.button() == Qt.MouseButton.RightButton:
            self.session.key("GoBack")

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if e.buttons() & Qt.MouseButton.LeftButton:
            self.session.pointer(*self._uv(e.position()), True)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.session.pointer(*self._uv(e.position()), False)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.session.key("GoBack")
        elif e.text() and e.text().isascii() and e.text().isprintable():
            self.session.key(e.text())
        elif e.key() == Qt.Key.Key_Backspace:
            self.session.key("Backspace")
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.session.key("Enter")


def phone_size(window: QWidget) -> tuple[int, int]:
    screen = window.screen() or QGuiApplication.primaryScreen()
    avail = screen.availableGeometry()
    h = min(int((avail.height() - 160) / 16) * 16, int((avail.width() * 0.5) / 9) * 16)
    return (h * 9 // 16 // 2 * 2, h)


def main() -> int:
    app = QApplication(sys.argv)
    gpu = GpuMode(sys.argv[1]) if len(sys.argv) > 1 else GpuMode.HOST
    window = QMainWindow()
    window.setWindowTitle("Orcus spike viewer")
    size = phone_size(window)
    bridge = Bridge()
    session = Session(gpu, bridge, size)
    view = PhoneView(session, size)
    status = QLabel("")
    text = QLineEdit()
    text.setPlaceholderText("한글 입력 → 휴대폰에 붙여넣기")
    paste_key = QPushButton("붙여넣기(Paste 키)")
    paste_ctrl = QPushButton("붙여넣기(Ctrl+V)")
    keys = QHBoxLayout()
    for label, name in (("◁", "GoBack"), ("○", "GoHome"), ("▢", "AppSwitch")):
        b = QPushButton(label)
        b.clicked.connect(lambda _=False, n=name: session.key(n))
        keys.addWidget(b)
    paste_key.clicked.connect(lambda: threading.Thread(target=session.paste, args=(text.text(), False), daemon=True).start())
    paste_ctrl.clicked.connect(lambda: threading.Thread(target=session.paste, args=(text.text(), True), daemon=True).start())
    bar = QHBoxLayout()
    bar.addWidget(text, 1)
    bar.addWidget(paste_key)
    bar.addWidget(paste_ctrl)
    root = QVBoxLayout()
    root.addWidget(view, 0, Qt.AlignmentFlag.AlignHCenter)
    root.addLayout(keys)
    root.addLayout(bar)
    root.addWidget(status)
    holder = QWidget()
    holder.setLayout(root)
    window.setCentralWidget(holder)
    bridge.frame.connect(view.show_latest, Qt.ConnectionType.QueuedConnection)
    bridge.status.connect(status.setText, Qt.ConnectionType.QueuedConnection)

    last = {"frames": 0, "t": time.monotonic()}

    def tick() -> None:
        now = time.monotonic()
        fps = (session.frames - last["frames"]) / max(1e-3, now - last["t"])
        last.update(frames=session.frames, t=now)
        delay = f" · 누름→프레임 {session.last_delay_ms:.0f} ms" if session.last_delay_ms is not None else ""
        if session.live is not None and session.live.stub is not None:
            status.setText(f"{size[0]}x{size[1]} · {fps:.0f} fps{delay}")
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(1000)
    app.aboutToQuit.connect(session.close)
    window.show()
    session.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
