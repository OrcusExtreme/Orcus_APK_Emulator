"""Frame pump (ARCHITECTURE.md §9.1): the frame callback, run on the `frames` thread.

The one exception to the callback rule (§3): it wraps the RGB888 bytes in a QImage and converts to RGB32 there,
then keeps only the newest frame in a one-slot mailbox. The main thread is signalled once per paint; frames that
arrive while a paint is pending replace the one waiting. No widget is touched here.
"""

import threading
import time
from dataclasses import dataclass

from PySide6.QtGui import QImage

from orcus.contracts import Frame, Rotation

from .bridge import Bridge


@dataclass(frozen=True)
class Shown:
    image: QImage
    seq: int
    rotation: Rotation
    width: int
    height: int


class FramePump:
    def __init__(self, bridge: Bridge) -> None:
        self._bridge = bridge
        self._lock = threading.Lock()
        self._latest: Shown | None = None
        self._pending = False
        self.dropped = 0          # frames replaced before they were painted
        self.last_seq = -1
        self.gaps = 0             # seq jumps (frames the server skipped)
        self.age_ms = 0.0         # now - timestampUs of the newest frame

    def on_frame(self, f: Frame) -> None:   # frames thread
        img = QImage(f.rgb888, f.width, f.height, f.width * 3, QImage.Format.Format_RGB888)
        img = img.convertToFormat(QImage.Format.Format_RGB32)   # deep copy: f.rgb888 may go
        shown = Shown(img, f.seq, f.rotation, f.width, f.height)
        with self._lock:
            if self._pending:
                self.dropped += 1
            if self.last_seq >= 0 and f.seq > self.last_seq + 1:
                self.gaps += f.seq - self.last_seq - 1
            self.last_seq = f.seq
            self.age_ms = max(0.0, time.time() * 1000 - f.ts_us / 1000)
            self._latest = shown
            signal = not self._pending
            self._pending = True
        if signal:
            self._bridge.frame.emit()

    def take(self) -> Shown | None:   # main thread, when painting
        with self._lock:
            self._pending = False
            return self._latest

    def peek(self) -> Shown | None:
        with self._lock:
            return self._latest

    def clear(self) -> None:
        with self._lock:
            self._latest = None
            self._pending = False
            self.last_seq = -1
