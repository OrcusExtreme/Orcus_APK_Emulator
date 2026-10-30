"""Input queue (ARCHITECTURE.md §9.3–§9.4, §9.6) on the `grpc-input` thread: one streamInputEvent call.

- Pointer: frame-normalized point + that frame's seq -> panel pixels (§9.2) -> MouseEvent. S7 showed the guest
  keeps a finger down until buttons=0 arrives, and a later "press" elsewhere is only a move of that finger, so
  release_all() sends the release whenever a button is held (focus loss, rotation, reconnect, epoch change).
- Keys: always keypress (the default keydown never comes up). Long press = keydown, 700 ms, keyup.
- Text: Korean and ASCII alike go through paste — setClipboard, 100 ms, the Paste key, in queue order
  (S8: Ctrl+V types 'ㅍ', and ASCII key events turn into jamo on the ko-KR Gboard).
- Wheel: the guest has no wheel device (S7); [P3b] emulates it with a short drag. Here it is ignored.
"""

import queue
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import grpc

from orcus.common import log
from orcus.contracts import HwKey, PointerAt, Rotation
from orcus.emugrpc.generated import emulator_controller_pb2 as ec

from .rotation import to_panel

_log = log.get("device.input")
PASTE_SETTLE_S = 0.1
LONG_PRESS_S = 0.7

_KP, _DOWN, _UP = ec.KeyboardEvent.keypress, ec.KeyboardEvent.keydown, ec.KeyboardEvent.keyup


class InputQueue:
    """InputSink. Every method only enqueues and returns."""

    def __init__(self, stub: Any, panel: tuple[int, int], rotation_of: Callable[[int], Rotation]) -> None:
        self._stub = stub
        self._panel = panel
        self._rotation_of = rotation_of
        self._q: "queue.Queue[tuple[str, Any] | None]" = queue.Queue()
        self._pressed = False
        self._last = (0, 0)
        self._closed = threading.Event()
        self.sent: int = 0
        self._thread = threading.Thread(target=self._pump, name="grpc-input", daemon=True)
        self._thread.start()

    # InputSink ---------------------------------------------------------------------------------------------------
    def pointer(self, at: PointerAt, pressed: bool) -> None:
        self._q.put(("pointer", (at, pressed)))

    def wheel(self, at: PointerAt, dy: int) -> None:
        pass   # no wheel device in the guest (S7); emulated with a drag in [P3b]

    def key(self, key: HwKey | str, *, long_press: bool = False) -> None:
        self._q.put(("key", (str(key), long_press)))

    def chord(self, modifier: str, key: str) -> None:
        self._q.put(("chord", (modifier, key)))

    def type_ascii(self, text: str) -> None:
        self.paste_text(text)   # key events become jamo with the Korean keyboard (S8)

    def paste_text(self, text: str) -> None:
        self._q.put(("paste", text))

    def release_all(self) -> None:
        self._q.put(("release", None))

    def close(self) -> None:
        """Release a held button, then end the stream."""
        self.release_all()
        self._q.put(None)
        self._closed.set()
        self._thread.join(3)

    # grpc-input --------------------------------------------------------------------------------------------------
    def _pump(self) -> None:
        backoff = 0.2
        while not self._closed.is_set() or not self._q.empty():
            try:
                self._stub.streamInputEvent(self._events(), timeout=None)
                return   # the generator ended: closed
            except grpc.RpcError as e:
                if self._closed.is_set():
                    return
                _log.warning("input stream broke (%s); reconnecting", e.code())
                self._pressed = False   # the guest released with the stream, nothing to release
                time.sleep(backoff)
                backoff = min(backoff * 2, 3.0)

    def _events(self) -> Iterator[ec.InputEvent]:
        while True:
            item = self._q.get()
            if item is None:
                if self._pressed:
                    yield self._mouse(*self._last, 0)
                return
            kind, payload = item
            match kind:
                case "pointer":
                    at, pressed = payload
                    x, y = to_panel(self._rotation_of(at.frame_seq), at.u, at.v, *self._panel)
                    if not pressed and not self._pressed:
                        continue   # a release without a press: nothing to undo
                    self._pressed = pressed
                    self._last = (x, y)
                    yield self._mouse(x, y, 1 if pressed else 0)
                case "release":
                    if self._pressed:
                        self._pressed = False
                        yield self._mouse(*self._last, 0)
                case "key":
                    name, long_press = payload
                    if long_press:
                        yield self._key(name, _DOWN)
                        time.sleep(LONG_PRESS_S)
                        yield self._key(name, _UP)
                    else:
                        yield self._key(name, _KP)
                case "chord":
                    modifier, key = payload
                    yield self._key(modifier, _DOWN)
                    yield self._key(key, _KP)
                    yield self._key(modifier, _UP)
                case "paste":
                    try:
                        self._stub.setClipboard(ec.ClipData(text=payload), timeout=5)
                    except grpc.RpcError as e:
                        _log.warning("setClipboard failed: %s", e.code())
                        continue
                    time.sleep(PASTE_SETTLE_S)
                    yield self._key("Paste", _KP)
                case _:
                    pass

    def _mouse(self, x: int, y: int, buttons: int) -> ec.InputEvent:
        self.sent += 1
        return ec.InputEvent(mouse_event=ec.MouseEvent(x=x, y=y, buttons=buttons))

    def _key(self, name: str, kind: "ec.KeyboardEvent.KeyEventType") -> ec.InputEvent:
        self.sent += 1
        return ec.InputEvent(key_event=ec.KeyboardEvent(eventType=kind, key=name))
