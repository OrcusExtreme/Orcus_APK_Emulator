"""The phone screen (ARCHITECTURE.md §9.1–§9.4): the newest frame drawn 1:1, mouse and keys turned into input.

Pointer positions go out as normalized coordinates inside the frame on screen plus that frame's seq; the device
package maps them to panel pixels with that frame's rotation (§9.2). A held finger is always lifted (§9.3): on
release, when the pointer leaves the screen (at the edge), on focus loss, and when the frame rotation changes.
The wheel scrolls (a real scroll event, 3b); the 한/영 key opens the Korean input bar (§9.6).
"""

import time
from collections import deque
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFocusEvent,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from orcus.contracts import HwKey, InputSettings, InputSink, PointerAt, Rotation

from .frame_pump import FramePump, Shown
from .keymap import CHORD_KEYS, NAMED_KEYS, VK_HANGUL

BACKGROUND = QColor(14, 14, 18)


def fit(avail: QSize, *, chrome: QSize, landscape: bool, dpr: float) -> QSize:
    """Largest exact 9:16 (16:9) screen that fits, in logical px (§9.1). Never more than 720x1280 physical."""
    w_room, h_room = avail.width() - chrome.width(), avail.height() - chrome.height()
    if landscape:
        w_room, h_room = h_room, w_room   # sized as portrait, then turned
    k = max(1, min(h_room // 16, w_room // 9, int(1280 / dpr) // 16, int(720 / dpr) // 9))
    return QSize(16 * k, 9 * k) if landscape else QSize(9 * k, 16 * k)


class ScreenView(QWidget):
    hangul = Signal()   # the 한/영 key: open the Korean input bar

    def __init__(self, pump: FramePump, settings: Callable[[], InputSettings]) -> None:
        super().__init__()
        self._pump = pump
        self._settings = settings
        self._input: InputSink | None = None
        self._shown: Shown | None = None
        self._pressed = False
        self._last_at: PointerAt | None = None
        self._paints: deque[float] = deque(maxlen=60)
        self.overlay = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    # --- frames ---------------------------------------------------------------------------------------------------
    def set_input(self, sink: InputSink | None) -> None:
        self.release()
        self._input = sink

    def show_frame(self) -> Rotation | None:
        """bridge.frame: take the newest frame. Returns the rotation when it changed (the caller resizes)."""
        shown = self._pump.take()
        if shown is None:
            return None
        old = self._shown
        self._shown = shown
        self.update()
        if old is None or old.rotation != shown.rotation:
            self.release()
            return shown.rotation
        return None

    def clear(self) -> None:
        self._shown = None
        self.update()

    @property
    def rotation(self) -> Rotation:
        return self._shown.rotation if self._shown else Rotation.PORTRAIT

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        shown = self._shown
        if shown is None:
            p.fillRect(self.rect(), BACKGROUND)
            return
        img = shown.image
        img.setDevicePixelRatio(self.devicePixelRatioF())
        p.drawImage(0, 0, img)   # 1:1, no scaling or smoothing
        drawn = img.deviceIndependentSize()
        if drawn.width() < self.width() or drawn.height() < self.height():   # between a resize and its frame
            p.fillRect(QRect(int(drawn.width()), 0, self.width(), self.height()), BACKGROUND)
            p.fillRect(QRect(0, int(drawn.height()), self.width(), self.height()), BACKGROUND)
        self._paints.append(time.monotonic())
        if self.overlay:
            fps = (len(self._paints) - 1) / max(1e-3, self._paints[-1] - self._paints[0]) if len(self._paints) > 1 \
                else 0.0
            p.setPen(QColor(0, 255, 0))
            p.drawText(8, 18, f"{fps:4.1f} fps · age {self._pump.age_ms:5.1f} ms · gaps {self._pump.gaps} · "
                              f"dropped {self._pump.dropped} · {shown.width}x{shown.height} {shown.rotation.name}")

    # --- mouse (§9.3) -----------------------------------------------------------------------------------------------
    def _at(self, pos: QPointF) -> PointerAt:
        u = min(max(pos.x() / max(1, self.width()), 0.0), 1.0)
        v = min(max(pos.y() / max(1, self.height()), 0.0), 1.0)
        return PointerAt(u, v, self._shown.seq if self._shown else 0)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        sink = self._input
        if sink is None:
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        match event.button():
            case Qt.MouseButton.LeftButton:
                self._pressed = True
                self._last_at = self._at(event.position())
                sink.pointer(self._last_at, True)
            case Qt.MouseButton.RightButton if self._settings().right_click_back:
                sink.key(HwKey.BACK)
            case Qt.MouseButton.MiddleButton:
                sink.key(HwKey.HOME)
            case _:
                pass

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        sink = self._input
        if not self._pressed or sink is None:
            return
        pos = event.position()
        at = self._at(pos)
        if 0 <= pos.x() < self.width() and 0 <= pos.y() < self.height():
            self._last_at = at
            sink.pointer(at, True)   # every move at once: Canvas needs the real timing (§9.3)
        else:
            sink.pointer(at, False)  # left the screen: lift the finger at the edge
            self._pressed = False

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._pressed and self._input is not None:
            self._input.pointer(self._at(event.position()), False)
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = False

    def wheelEvent(self, event: QWheelEvent) -> None:
        sink = self._input
        dy = event.angleDelta().y()
        if sink is not None and dy and not self._pressed:   # not in the middle of a drag
            sink.wheel(self._at(event.position()), dy)
        event.accept()

    def release(self) -> None:
        """Lift a held finger (focus lost, window deactivated, rotation, epoch change)."""
        if self._pressed and self._input is not None and self._last_at is not None:
            self._input.pointer(self._last_at, False)
        self._pressed = False

    def focusOutEvent(self, event: QFocusEvent) -> None:
        self.release()
        super().focusOutEvent(event)

    # --- keys (§9.4) ------------------------------------------------------------------------------------------------
    def focusNextPrevChild(self, next: bool) -> bool:
        return False   # Tab belongs to the phone while the screen has focus

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.nativeVirtualKey() == VK_HANGUL or event.key() == Qt.Key.Key_Hangul:
            self.hangul.emit()
            return
        sink = self._input
        if sink is None:
            super().keyPressEvent(event)
            return
        key = Qt.Key(event.key())
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if key == Qt.Key.Key_Escape:
            if self._settings().esc_is_back:
                sink.key(HwKey.BACK)
            return
        if ctrl and key == Qt.Key.Key_V:   # host clipboard, Korean included (§9.4)
            text = QGuiApplication.clipboard().text()
            if text:
                sink.paste_text(text)
            return
        if ctrl and not shift and key in CHORD_KEYS:
            sink.chord("Control", CHORD_KEYS[key])
            return
        if key in NAMED_KEYS:
            name = NAMED_KEYS[key]
            if ctrl or shift:
                sink.chord("Control" if ctrl else "Shift", name)
            else:
                sink.key(name)
            return
        text = event.text()
        if text and text.isprintable() and not ctrl:
            sink.type_ascii(text) if text.isascii() else sink.paste_text(text)
            return
        super().keyPressEvent(event)
