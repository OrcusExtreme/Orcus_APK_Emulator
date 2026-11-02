"""Input queue (ARCHITECTURE.md §9.3–§9.4, §9.6) on the `grpc-input` thread: one streamInputEvent call.

- Pointer: frame-normalized point + that frame's seq -> panel pixels (§9.2) -> MouseEvent. S7 showed the guest
  keeps a finger down until buttons=0 arrives, and a later "press" elsewhere is only a move of that finger, so
  release_all() sends the release whenever a button is held (focus loss, rotation, reconnect, epoch change).
  A broken stream is not trusted to have lifted it either: a new stream starts with the release (§12.3 item 6).
- Only the current stream takes from the queue: gRPC keeps pulling a broken call's request iterator once more,
  and that pull would swallow the first input after a reconnect.
- Keys: always keypress (the default keydown never comes up). Long press = keydown, 700 ms, keyup.
- Text: Korean and ASCII alike go through paste — setClipboard, 100 ms, the Paste key, in queue order
  (S8: Ctrl+V types 'ㅍ', and ASCII key events turn into jamo on the ko-KR Gboard). Pastes waiting in the queue
  go as one, and the next setClipboard waits 100 ms after a Paste, or fast typing comes out of order.
- Wheel: the guest has no wheel device and gRPC injectWheel does not scroll (S7). `input mouse scroll` does: it
  injects ACTION_SCROLL, which lists and scrollable screens take and a Canvas ignores. It needs adb, so the
  app injects the sink; notches add up on their own thread while one command (~60 ms) runs.
- Several fingers (a pinch): TouchEvent, one Touch per finger with ids 0..n-1, in the same panel pixels as
  the mouse; pressure 0 lifts a finger (the emulator keeps it down otherwise). The mouse finger and the touch
  fingers are never down together: whichever comes lifts the other first. Lifted like the mouse finger on
  release_all, at a new stream and at the end.
"""

import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import grpc

from orcus.common import log
from orcus.contracts import HwKey, PointerAt, Rotation
from orcus.emugrpc.generated import emulator_controller_pb2 as ec

from .rotation import to_panel

_log = log.get("device.input")
PASTE_SETTLE_S = 0.1
PASTE_MAX = 64 * 1024   # characters; more would block taps behind it and flood a text box
LONG_PRESS_S = 0.7
WHEEL_NOTCH = 120       # Qt angleDelta units per notch; one notch = VSCROLL 1 = 128 px in a ScrollView
TOUCH_PRESSURE = 512    # any non-zero value is a finger down (0..1024)

_KP, _DOWN, _UP = ec.KeyboardEvent.keypress, ec.KeyboardEvent.keydown, ec.KeyboardEvent.keyup
_STALE = ("stale", None)

type ScrollSink = Callable[[int, int, float], None]   # display x, y, VSCROLL (+ = up); runs on device-wheel


class _Restart(Exception):
    """Ends the request iterator: gRPC fails the call and _pump opens a new stream."""


class _WheelPump:
    """Hands wheel notches to the sink on its own thread; notches that arrive meanwhile add up (newest point)."""

    def __init__(self, sink: ScrollSink) -> None:
        self._sink = sink
        self._cv = threading.Condition()
        self._pending: tuple[int, int, float] | None = None
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="device-wheel", daemon=True)
        self._thread.start()

    def add(self, x: int, y: int, amount: float) -> None:
        with self._cv:
            if self._pending is not None:
                amount += self._pending[2]
            self._pending = (x, y, amount)
            self._cv.notify()

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify()
        self._thread.join(2)

    def _run(self) -> None:
        while True:
            with self._cv:
                while self._pending is None and not self._closed:
                    self._cv.wait()
                if self._closed:
                    return
                assert self._pending is not None
                x, y, amount = self._pending
                self._pending = None
            if abs(amount) >= 0.01:
                try:
                    self._sink(x, y, round(amount, 2))
                except Exception:
                    _log.exception("scroll failed")


class InputQueue:
    """InputSink. Every method only enqueues and returns."""

    def __init__(self, stub: Any, panel: tuple[int, int], rotation_of: Callable[[int], Rotation],
                 scroll: ScrollSink | None = None) -> None:
        self._stub = stub
        self._panel = panel
        self._rotation_of = rotation_of
        self._items: deque[tuple[str, Any] | None] = deque()
        self._cv = threading.Condition()
        self._stream = 0             # the stream generation allowed to take items
        self._pressed = False
        self._last = (0, 0)
        self._fingers: list[tuple[int, int]] = []   # touch fingers down, by id
        self._quiet_until = 0.0      # no setClipboard before the last Paste had its time
        self._closed = threading.Event()
        self._wheel = _WheelPump(scroll) if scroll is not None else None
        self.sent: int = 0
        self._thread = threading.Thread(target=self._pump, name="grpc-input", daemon=True)
        self._thread.start()

    # InputSink ---------------------------------------------------------------------------------------------------
    def pointer(self, at: PointerAt, pressed: bool) -> None:
        self._put(("pointer", (at, pressed)))

    def touches(self, points: Sequence[PointerAt]) -> None:
        self._put(("touches", tuple(points)))

    def wheel(self, at: PointerAt, dy: int) -> None:
        if self._wheel is not None and dy:
            self._wheel.add(*self._display_point(at), dy / WHEEL_NOTCH)

    def key(self, key: HwKey | str, *, long_press: bool = False) -> None:
        self._put(("key", (str(key), long_press)))

    def chord(self, modifier: str, key: str) -> None:
        self._put(("chord", (modifier, key)))

    def type_ascii(self, text: str) -> None:
        self.paste_text(text)   # key events become jamo with the Korean keyboard (S8)

    def paste_text(self, text: str) -> None:
        if len(text) > PASTE_MAX:   # a huge clipboard would block the input queue for seconds and flood the guest
            _log.info("paste cut from %d to %d characters", len(text), PASTE_MAX)
            text = text[:PASTE_MAX]
        if text:
            self._put(("paste", text))

    def release_all(self) -> None:
        self._put(("release", None))

    def close(self) -> None:
        """Release a held button, then end the stream."""
        self.release_all()
        self._put(None)
        self._closed.set()
        self._thread.join(3)
        if self._wheel is not None:
            self._wheel.close()

    def restart_stream(self) -> None:
        """Drop the stream in queue order and open a new one, as after a broken connection (device test hook)."""
        self._put(("restart", None))

    # helpers ------------------------------------------------------------------------------------------------------
    def _put(self, item: tuple[str, Any] | None) -> None:
        with self._cv:
            self._items.append(item)
            self._cv.notify_all()

    def _display_point(self, at: PointerAt) -> tuple[int, int]:
        """`input` takes coordinates in the rotated display. The display is taken to follow the posture of the
        frame (it does, except at 180° and on locked screens, where the mismatch banner offers to turn)."""
        w, h = self._panel
        u, v = min(max(at.u, 0.0), 1.0), min(max(at.v, 0.0), 1.0)
        match self._rotation_of(at.frame_seq):
            case Rotation.LANDSCAPE | Rotation.REVERSE_LANDSCAPE:
                return round(u * (h - 1)), round(v * (w - 1))
            case Rotation.REVERSE_PORTRAIT:   # Android does not turn the display to 180°: the frame is upside down
                return round((1 - u) * (w - 1)), round((1 - v) * (h - 1))
            case _:
                return round(u * (w - 1)), round(v * (h - 1))

    def _take(self, gen: int) -> tuple[str, Any] | None:
        """The next item for stream `gen`, _STALE once a newer stream exists. Waiting pastes come as one."""
        with self._cv:
            while not self._items and self._stream == gen:
                self._cv.wait()
            if self._stream != gen:
                return _STALE
            item = self._items.popleft()
            if item is not None and item[0] == "paste":
                text = item[1]
                while self._items and (nxt := self._items[0]) is not None and nxt[0] == "paste" \
                        and len(text) + len(nxt[1]) <= PASTE_MAX:
                    text += nxt[1]
                    self._items.popleft()
                item = ("paste", text)
            return item

    # grpc-input --------------------------------------------------------------------------------------------------
    def _pump(self) -> None:
        backoff = 0.2
        while True:
            with self._cv:
                if self._closed.is_set() and not self._items:
                    return
                gen = self._stream
            try:
                self._stub.streamInputEvent(self._events(gen), timeout=None)
                return   # the generator ended: closed
            except grpc.RpcError as e:
                with self._cv:
                    self._stream += 1   # the broken call's iterator, still waiting, gives up without taking
                    self._cv.notify_all()
                if self._closed.is_set():
                    return
                _log.warning("input stream broke (%s); reconnecting", e.code())
                time.sleep(backoff)
                backoff = min(backoff * 2, 3.0)

    def _lift(self) -> Iterator[ec.InputEvent]:
        """Every finger down, mouse or touch, up."""
        if self._pressed:
            self._pressed = False
            yield self._mouse(*self._last, 0)
        if self._fingers:
            yield self._touch([])

    def _events(self, gen: int) -> Iterator[ec.InputEvent]:
        yield from self._lift()   # the old stream may have left a finger down: lift it before anything else
        while True:
            item = self._take(gen)
            if item is _STALE:
                return
            if item is None:
                yield from self._lift()
                return
            kind, payload = item
            match kind:
                case "pointer":
                    at, pressed = payload
                    x, y = to_panel(self._rotation_of(at.frame_seq), at.u, at.v, *self._panel)
                    if not pressed and not self._pressed:
                        continue   # a release without a press: nothing to undo
                    if self._fingers:
                        yield self._touch([])
                    self._pressed = pressed
                    self._last = (x, y)
                    yield self._mouse(x, y, 1 if pressed else 0)
                case "touches":
                    if not payload and not self._fingers:
                        continue
                    if self._pressed:
                        self._pressed = False
                        yield self._mouse(*self._last, 0)
                    yield self._touch([to_panel(self._rotation_of(at.frame_seq), at.u, at.v, *self._panel)
                                       for at in payload])
                case "release":
                    yield from self._lift()
                case "restart":
                    raise _Restart()
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
                    wait = self._quiet_until - time.monotonic()
                    if wait > 0:
                        time.sleep(wait)
                    try:
                        self._stub.setClipboard(ec.ClipData(text=payload), timeout=5)
                    except grpc.RpcError as e:
                        _log.warning("setClipboard failed: %s", e.code())
                        continue
                    time.sleep(PASTE_SETTLE_S)
                    yield self._key("Paste", _KP)
                    self._quiet_until = time.monotonic() + PASTE_SETTLE_S   # runs once gRPC took the Paste
                case _:
                    pass

    def _mouse(self, x: int, y: int, buttons: int) -> ec.InputEvent:
        self.sent += 1
        return ec.InputEvent(mouse_event=ec.MouseEvent(x=x, y=y, buttons=buttons))

    def _touch(self, points: list[tuple[int, int]]) -> ec.InputEvent:
        """Fingers 0..n-1 at `points`; the ones down before and not listed now lift where they were."""
        touches = [ec.Touch(x=x, y=y, identifier=i, pressure=TOUCH_PRESSURE) for i, (x, y) in enumerate(points)]
        touches += [ec.Touch(x=x, y=y, identifier=i, pressure=0)
                    for i, (x, y) in enumerate(self._fingers) if i >= len(points)]
        self._fingers = points
        self.sent += 1
        return ec.InputEvent(touch_event=ec.TouchEvent(touches=touches))

    def _key(self, name: str, kind: "ec.KeyboardEvent.KeyEventType") -> ec.InputEvent:
        self.sent += 1
        return ec.InputEvent(key_event=ec.KeyboardEvent(eventType=kind, key=name))
