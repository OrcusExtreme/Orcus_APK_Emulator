"""Device surface: frames, input, sensors, logcat (implemented by orcus.device over emulator gRPC)."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Protocol

from .errors import OrcusError


class Rotation(IntEnum):
    PORTRAIT = 0
    LANDSCAPE = 1
    REVERSE_PORTRAIT = 2
    REVERSE_LANDSCAPE = 3


class HwKey(StrEnum):
    BACK = "GoBack"
    HOME = "GoHome"
    RECENTS = "AppSwitch"
    POWER = "Power"
    VOL_UP = "AudioVolumeUp"
    VOL_DOWN = "AudioVolumeDown"


class Sensitivity(StrEnum):
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


@dataclass(frozen=True, slots=True)
class Frame:
    """RGB888, rows top to bottom (confirmed in S2), stride = width * 3."""

    rgb888: bytes
    width: int
    height: int
    rotation: Rotation
    seq: int
    ts_us: int


@dataclass(frozen=True, slots=True)
class PointerAt:
    """Normalized position inside the displayed frame plus that frame's seq.

    Panel-pixel conversion (rotation, panel size) belongs to orcus.device.
    """

    u: float
    v: float
    frame_seq: int


@dataclass(frozen=True)
class LogEntry:
    ts_us: int
    pid: int
    tid: int
    level: str
    tag: str
    msg: str


class CancellableIter[T](Protocol):
    """Normal end and cancel raise StopIteration; failures raise OrcusFailure."""

    def __iter__(self) -> Iterator[T]: ...
    def cancel(self) -> None: ...


class FrameSource(Protocol):
    def start(self, width: int, height: int, on_frame: Callable[[Frame], None],
              on_end: Callable[[OrcusError | None], None]) -> None: ...
    def retarget(self, width: int, height: int) -> None: ...   # cancel the stream and request again
    def stop(self) -> None: ...


class InputSink(Protocol):
    """Every call only enqueues and returns."""

    def pointer(self, at: PointerAt, pressed: bool) -> None: ...
    def wheel(self, at: PointerAt, dy: int) -> None: ...
    def key(self, key: HwKey | str, *, long_press: bool = False) -> None: ...   # always a keypress
    def chord(self, modifier: str, key: str) -> None: ...                      # e.g. ("Control", "a")
    def type_ascii(self, text: str) -> None: ...
    def paste_text(self, text: str) -> None: ...    # setClipboard -> 100 ms -> Paste, in order
    def release_all(self) -> None: ...


class DeviceControls(Protocol):
    """Everything goes to the device-controls FIFO thread and returns at once."""

    def shake(self, sensitivity: Sensitivity) -> None: ...
    def rotate(self, to: Rotation) -> None: ...
    def tilt(self, pitch_deg: float, roll_deg: float) -> None: ...
    def set_location(self, lat: float, lon: float, alt_m: float = 30.0) -> None: ...
    def set_microphone(self, enabled: bool) -> None: ...


class DeviceSession(Protocol):
    epoch: int
    frames: FrameSource
    input: InputSink
    controls: DeviceControls | None   # None for backends without sensors

    def logcat(self) -> CancellableIter[LogEntry]: ...
    def screenshot_png(self, on_done: Callable[[bytes | None], None]) -> None: ...   # non-blocking (device-io)
    def close(self) -> None: ...
    # close: release_all -> cancel streams -> close channel (engine calls it in SESSION_INIT, STOPPING, FAILED)
